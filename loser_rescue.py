"""
Loser Rescue Intelligence Layer.

Purpose: replace the binary "close any loser >= 70% of SL distance" rule in
smart_bucket_close with a per-position recovery-probability score.

Inputs: an open MT5 position, a live MFE/MAE tracker dict (kept by the bot),
the strategy_health dict (for per-(sym, strat) live recovery rate).

Output: RescueDecision = {"action": "keep"|"close", "score": int, "reasons": [...]}

Score = weighted blend of:
  recovery_rate (35%)  — historical per-(sym, strat) recovery rate, bootstrap by
                          family until N>=RESCUE_MIN_SAMPLES
  mfe_proximity (25%)  — how close did this trade get to TP before reversing?
  mtf_alignment (20%)  — does the higher-TF EMA slope still agree with original entry?
  regime        (10%)  — ATR percentile (extremes favor mean reversion → keep)
  consumed_pct  (10%)  — deeper into SL → lower score (soft cap)

Hard rule: if consumed_pct >= RESCUE_HARD_CLOSE_PCT, always close regardless of score.

This module NEVER opens trades, NEVER modifies SL/TP, NEVER changes lot sizes.
It only decides keep-vs-close for losers the smart-bucket-close is about to sweep.
"""

import csv
import os
from datetime import datetime, timezone

import MetaTrader5 as mt5

from d1_portfolio_config import (RESCUE_HARD_CLOSE_PCT, RESCUE_KEEP_THRESHOLD,
                                  RESCUE_LOG_DECISIONS, RESCUE_MIN_SAMPLES,
                                  USE_LOSER_RESCUE)
import strategy_health as health
from indicators import atr_at as _atr_at, ema as _ema


# ---------------------------------------------------------------------------
# Bootstrap defaults for recovery rate (used until live samples >= MIN_SAMPLES)
#
# CALIBRATED from backtests/backtest_rescue_recovery.py on 18,251 historical
# trades across 16 symbols over 8 years (D1). The measured rates were lower
# than initially expected — recovery of a near-SL loser is rare (~11-17%)
# regardless of strategy family. Mean-reversion does NOT recover materially
# more often than trend.
#
# However: keeping near-SL losers STILL beats the legacy bucket-close rule
# because the few recoveries are large (3:1 R:R donchian winners) while
# the extra cost of a rescue-failure is only -0.3R (from -0.7R to -1.0R).
# Break-even recovery for 3:1 strategies is ~7.5%; for 1:1 strategies ~15%.
# ---------------------------------------------------------------------------
_MEAN_REV_FAMILY = {"rsi2", "rsi2_H1", "bb_extreme", "bb_extreme_H1", "3day_reverse"}
_TREND_FAMILY    = {"donchian20", "donchian20_T", "donchian20_H1",
                    "momentum60", "momentum60_T", "momentum60_H1",
                    "consensus"}

# Measured recovery rates per strategy (used as the default until live samples
# >= RESCUE_MIN_SAMPLES). Sources:
#   D1 strategies        — backtests/backtest_rescue_recovery.py    (18,251 trades, 8 yrs)
#   H1 strategies        — backtests/backtest_h1_rescue_recovery.py (66,133 trades, ~620 days)
#   _T and consensus     — backtests/backtest_variants_rescue_recovery.py (7,938 trades, 8 yrs)
_MEASURED_RECOVERY = {
    # D1 base strategies
    "donchian20":     0.108,
    "momentum60":     0.139,
    "rsi2":           0.151,
    "3day_reverse":   0.165,
    "bb_extreme":     0.114,
    # H1 strategies
    "donchian20_H1":  0.091,
    "momentum60_H1":  0.114,
    "rsi2_H1":        0.163,
    "bb_extreme_H1":  0.102,
    # Tight-TP variants
    "donchian20_T":   0.128,
    "momentum60_T":   0.170,
    # Consensus multi-strategy filter
    "consensus":      0.141,
}


def _family_default_recovery(strat):
    # Exact match first
    if strat in _MEASURED_RECOVERY:
        return _MEASURED_RECOVERY[strat]
    # Unknown variant: try base-name prefix lookup
    for base, rate in _MEASURED_RECOVERY.items():
        if strat.startswith(base):
            return rate
    if strat in _MEAN_REV_FAMILY:
        return 0.14   # mean-rev family average across measurements
    if strat in _TREND_FAMILY:
        return 0.12   # trend family average across measurements
    return 0.13


# ---------------------------------------------------------------------------
# Sub-scores  — each returns 0-100
# ---------------------------------------------------------------------------
def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def _score_recovery_rate(health_state, sym, strat):
    """Live per-(sym, strat) recovery rate (0..1) scaled to 0-100. Falls back
    to family default if insufficient samples.

    Mapping: 0% -> 0, 10% -> 60, 17%+ -> 100. Calibrated against the 8-yr
    backtest break-even analysis: for 3:1 R:R trades, break-even recovery is
    ~7.5%; for 1:1 R:R it's ~15%. So 10%+ recovery is already a clear keep
    signal in expectation, and we map it accordingly."""
    live = health.recovery_rate(health_state, sym, strat, min_samples=RESCUE_MIN_SAMPLES)
    rate = live if live is not None else _family_default_recovery(strat)
    return _clamp(rate * 600.0)


def _score_mfe_proximity(position, mfe_mae_state):
    """How far did this position travel toward TP before reversing?
    Closer to TP at MFE = stronger mean-reversion case for letting it ride back.

    Returns 0 (never went anywhere good) to 100 (touched TP, came back)."""
    entry = position.price_open
    tp = position.tp
    if tp <= 0 or entry <= 0:
        return 30.0   # no TP set: neutral-low
    tp_dist = abs(tp - entry)
    if tp_dist <= 0:
        return 30.0
    info = mfe_mae_state.get(position.ticket)
    if info is None:
        return 30.0   # no tracking yet (just opened) — neutral-low
    if position.type == mt5.POSITION_TYPE_BUY:
        mfe_price = info.get("mfe_price", entry)
        favorable = max(0.0, mfe_price - entry)
    else:
        mfe_price = info.get("mfe_price", entry)   # for sells we store low
        favorable = max(0.0, entry - mfe_price)
    pct_to_tp = 100.0 * favorable / tp_dist
    # Mapping: 0% to TP -> 0,  50%+ -> 100
    return _clamp(pct_to_tp * 2.0)


def _direction_from_position(position):
    return "BUY" if position.type == mt5.POSITION_TYPE_BUY else "SELL"


def _score_mtf_alignment(position):
    """Higher-TF EMA slope alignment with original entry direction.
    Reuses the pattern from trade_intelligence._score_mtf_alignment without
    importing it (the TI version takes a TradePlan; we have a Position)."""
    strat = (position.comment or "").strip()
    is_h1 = strat.endswith("_H1")
    htf = mt5.TIMEFRAME_D1 if is_h1 else mt5.TIMEFRAME_H4
    try:
        bars = mt5.copy_rates_from_pos(position.symbol, htf, 0, 220)
    except Exception:
        return 60.0
    if bars is None or len(bars) < 210:
        return 60.0
    closes = [float(b['close']) for b in bars[-201:-1]]
    if len(closes) < 200:
        return 60.0

    e_now = _ema(closes, 100)
    e_old = _ema(closes[:-20], 100)
    if e_now is None or e_old is None:
        return 60.0
    trend_up = e_now > e_old
    direction = _direction_from_position(position)
    aligned = (trend_up and direction == "BUY") or (not trend_up and direction == "SELL")
    return 100.0 if aligned else 25.0


def _score_regime(position):
    """ATR percentile on the position's own timeframe. Extremes (very low or
    very high vol) favor mean reversion → keep. Mid-range = lower score."""
    strat = (position.comment or "").strip()
    tf = mt5.TIMEFRAME_H1 if strat.endswith("_H1") else mt5.TIMEFRAME_D1
    try:
        bars = mt5.copy_rates_from_pos(position.symbol, tf, 0, 120)
    except Exception:
        return 50.0
    if bars is None or len(bars) < 30:
        return 50.0

    # ATR at the latest bar
    cur = _atr_at(bars, len(bars) - 1, period=14)
    if cur is None or cur <= 0:
        return 50.0
    samples = []
    step = max(1, (len(bars) - 15) // 25)
    for end in range(15, len(bars), step):
        v = _atr_at(bars, end, period=14)
        if v is not None and v > 0:
            samples.append(v)
    if not samples:
        return 50.0
    below = sum(1 for v in samples if v < cur)
    pct = 100.0 * below / len(samples)
    # Inverted vs the pre-trade scorer: for RESCUE we want extremes (potential reversion)
    # 50th percentile = lowest score (40), <10 or >90 percentile = 100
    if pct <= 10 or pct >= 90:
        return 100.0
    if pct <= 30 or pct >= 70:
        return 75.0
    return 40.0


def _score_consumed_pct(consumed_pct):
    """Deeper into SL = lower score (acts as a soft cap).
    70% -> 100, 80% -> 60, 90% -> 20, >=95% -> 0."""
    if consumed_pct <= 70:
        return 100.0
    if consumed_pct >= 95:
        return 0.0
    # Linear from 100 @ 70 to 0 @ 95
    return _clamp(100.0 * (95.0 - consumed_pct) / 25.0)


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------
_WEIGHTS = {
    "recovery_rate": 35,
    "mfe_proximity": 25,
    "mtf_alignment": 20,
    "regime":        10,
    "consumed_pct":  10,
}


def evaluate(position, consumed_pct, health_state, mfe_mae_state):
    """Decide whether to keep or close a near-SL loser.

    Args:
        position: MT5 position object (already filtered to consumed_pct >= 70%)
        consumed_pct: float, % of SL distance consumed (0-100)
        health_state: the bot's strategy_health dict
        mfe_mae_state: dict ticket -> {"mfe_price", "mae_price", ...}

    Returns:
        dict with keys: action ("keep"|"close"), score (int 0-100),
                        reasons (list[str]), breakdown (dict subscore->value)
    """
    sym = position.symbol
    strat = (position.comment or "").strip() or "unknown"

    # Hard safety: deeply underwater → just close
    if consumed_pct >= RESCUE_HARD_CLOSE_PCT:
        return {
            "action":  "close",
            "score":   0,
            "reasons": [f"hard-close at {consumed_pct:.0f}%>={RESCUE_HARD_CLOSE_PCT:.0f}%"],
            "breakdown": {},
        }

    breakdown = {
        "recovery_rate": _score_recovery_rate(health_state, sym, strat),
        "mfe_proximity": _score_mfe_proximity(position, mfe_mae_state),
        "mtf_alignment": _score_mtf_alignment(position),
        "regime":        _score_regime(position),
        "consumed_pct":  _score_consumed_pct(consumed_pct),
    }
    total = sum(_WEIGHTS[k] * breakdown[k] for k in _WEIGHTS) / sum(_WEIGHTS.values())
    action = "keep" if total >= RESCUE_KEEP_THRESHOLD else "close"

    reasons = []
    # Surface the loudest contributors in plain language
    if breakdown["recovery_rate"] >= 70:
        live = health.recovery_rate(health_state, sym, strat, min_samples=RESCUE_MIN_SAMPLES)
        if live is not None:
            reasons.append(f"recovers {live*100:.0f}% live")
        else:
            reasons.append(f"family-default recovery {_family_default_recovery(strat)*100:.0f}%")
    if breakdown["mfe_proximity"] >= 70:
        reasons.append("touched >35% to TP earlier")
    if breakdown["mtf_alignment"] >= 90:
        reasons.append("HTF still agrees")
    elif breakdown["mtf_alignment"] <= 30:
        reasons.append("HTF against us")
    if breakdown["regime"] >= 90:
        reasons.append("extreme vol regime")
    if consumed_pct >= 85:
        reasons.append(f"deep at {consumed_pct:.0f}% of SL")

    return {
        "action":   action,
        "score":    int(round(total)),
        "reasons":  reasons,
        "breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
# CSV decision log
# ---------------------------------------------------------------------------
_LOG_DIR  = "logs"
_LOG_FILE = os.path.join(_LOG_DIR, "rescue_decisions.csv")
_LOG_HEADER = [
    "ts", "ticket", "sym", "strat", "consumed_pct", "score", "action",
    "recovery_rate", "mfe_proximity", "mtf_alignment", "regime", "consumed_pct_score",
    "reasons",
]


def log_decision(position, consumed_pct, decision):
    """Append one row per evaluation to rescue_decisions.csv."""
    if not RESCUE_LOG_DECISIONS:
        return
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        write_header = not os.path.exists(_LOG_FILE)
        with open(_LOG_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(_LOG_HEADER)
            bd = decision.get("breakdown", {})
            w.writerow([
                datetime.now(timezone.utc).isoformat(),
                position.ticket,
                position.symbol,
                (position.comment or "").strip(),
                f"{consumed_pct:.1f}",
                decision.get("score", 0),
                decision.get("action", ""),
                f"{bd.get('recovery_rate', 0):.0f}",
                f"{bd.get('mfe_proximity', 0):.0f}",
                f"{bd.get('mtf_alignment', 0):.0f}",
                f"{bd.get('regime', 0):.0f}",
                f"{bd.get('consumed_pct', 0):.0f}",
                "; ".join(decision.get("reasons", [])),
            ])
    except Exception as e:
        print(f"[rescue-log err] {e}")


# ---------------------------------------------------------------------------
# MFE/MAE tracker  — pure helpers; state lives in the bot
# ---------------------------------------------------------------------------
def update_mfe_mae(position, current_price, mfe_mae_state):
    """Per-tick: update high/low watermark for a position.
    For a BUY:   mfe_price = max favorable (highest) price seen
                 mae_price = max adverse  (lowest)  price seen
    For a SELL:  mfe_price = lowest price seen (favorable)
                 mae_price = highest price seen (adverse)

    Also stores the ORIGINAL SL captured the first time we see the ticket.
    This matters because trade_intelligence.migrate_position_stops moves SL
    to break-even+buffer at 50% to TP. After that move, the LIVE position.sl
    no longer reflects the trade's original risk — using it for consumed_pct
    would falsely flag a BE-protected winner that came back to entry as a
    "100% consumed loser" and force-close it."""
    entry = mfe_mae_state.get(position.ticket)
    if entry is None:
        entry = {
            "mfe_price":      current_price,
            "mae_price":      current_price,
            "first_70pct_ts": None,
            "opened_ts":      int(position.time),
            "symbol":         position.symbol,
            "strat":          (position.comment or "").strip(),
            "original_sl":    float(position.sl) if position.sl else 0.0,
            "entry_price":    float(position.price_open),
        }
        mfe_mae_state[position.ticket] = entry
        return

    if position.type == mt5.POSITION_TYPE_BUY:
        if current_price > entry["mfe_price"]:
            entry["mfe_price"] = current_price
        if current_price < entry["mae_price"]:
            entry["mae_price"] = current_price
    else:
        if current_price < entry["mfe_price"]:
            entry["mfe_price"] = current_price
        if current_price > entry["mae_price"]:
            entry["mae_price"] = current_price


def mark_70pct_touch_if_new(position, consumed_pct, mfe_mae_state, health_state):
    """If this is the first time this ticket has crossed 70%, mark it and
    increment the strategy_health near_sl_touches counter. De-duped per ticket.
    Returns True if this was the first crossing (counter incremented)."""
    if consumed_pct < 70.0:
        return False
    entry = mfe_mae_state.get(position.ticket)
    if entry is None:
        return False
    if entry.get("first_70pct_ts") is not None:
        return False
    entry["first_70pct_ts"] = int(datetime.now(timezone.utc).timestamp())
    sym = position.symbol
    strat = (position.comment or "").strip() or "unknown"
    health.record_near_sl_touch(health_state, sym, strat)
    return True


def resolve_closed_ticket(ticket, realized_pnl, mfe_mae_state, health_state):
    """Called when a tracked ticket closes. If it had ever touched 70%-SL,
    record whether it recovered (closed positive OR closed in mild-loser range).

    `recovered` rule: closed at profit OR pnl_pct of original risk less negative
    than -0.5 (i.e. came back from deep loss to small loss).

    NOTE: full consumed-pct at close is hard to know after close; we approximate
    using realized P&L direction. A positive close after a 70%-SL touch is a
    clear recovery. A small-loss close (we use realized > -50% of typical risk
    as a heuristic) is also counted as partial recovery."""
    entry = mfe_mae_state.pop(ticket, None)
    if entry is None or entry.get("first_70pct_ts") is None:
        return
    sym = entry.get("symbol")
    strat = entry.get("strat")
    if not sym or not strat:
        return
    # Recovered if closed at any profit. (Conservative definition; matches the
    # plan's "closed profitably" branch. The "closed above 50% consumed" branch
    # would need live SL distance we no longer have post-close.)
    recovered = realized_pnl > 0
    health.record_recovery_outcome(health_state, sym, strat, recovered)


# ---------------------------------------------------------------------------
# Convenience flag for the bot
# ---------------------------------------------------------------------------
def is_enabled():
    return bool(USE_LOSER_RESCUE)
