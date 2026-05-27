"""
Trade Intelligence Layer.

Two responsibilities:

A) PRE-TRADE QUALITY SCORE — compute_quality_score(plan, bars, sym_info, state)
   Returns a 0-100 score for a TradePlan before it's submitted. Six components:
     live_wr_score   25%  — live WR vs backtest expectation
     health_score    15%  — strategy_health.is_active()
     spread_score    15%  — spread cost vs TP distance
     regime_score    15%  — ATR percentile (avoid extremes)
     mtf_alignment   15%  — higher-TF trend agrees with plan
     concurrent_vote 15%  — other strategies on same symbol agreeing
   Bot calls this and drops the plan if score < QUALITY_THRESHOLD.

B) POST-ENTRY SL MIGRATION — migrate_position_stops(positions)
   Iterates open positions. Two actions per position:
     1) BE move:  when floating profit ≥ 50% of TP distance, move SL to entry+small buffer.
     2) Age decay: when position is > 70% of max_hold without resolution and currently
                   profitable, tighten TP toward current price to lock partial win.
   Returns count of positions modified this tick.

NEVER opens trades. NEVER changes lot sizes. NEVER changes entry decisions.
"""

import os
import csv
import time
from datetime import datetime, timezone
from typing import Optional

import MetaTrader5 as mt5

from d1_portfolio_config import (BB_PERIOD, BB_STD, MAX_TP_PCT_FX,
                                  MAX_TP_PCT_INDICES, MAX_TP_PCT_METALS,
                                  QUALITY_THRESHOLD, QUALITY_VERBOSE,
                                  SL_MIGRATION_BUFFER_PT, SL_MIGRATION_TRIGGER,
                                  STRAT_PARAMS, USE_MAX_TP_PCT,
                                  USE_QUALITY_FILTER, USE_SL_MIGRATION)
from d1_portfolio_strategy import STRATEGY_DETECTORS
from indicators import atr, bollinger, sma, ema as _ema
import strategy_health as health


# ============================================================================
# Backtest expectation table (mirrored from analytics_agent.py to avoid
# cross-import; ok if these drift slightly — used only for component scoring).
# ============================================================================
BACKTEST_EXPECTATION = {
    "donchian20":    35, "momentum60":    47, "rsi2":           60,
    "3day_reverse":  61, "bb_extreme":    42, "donchian20_T":   47,
    "momentum60_T":  55, "donchian20_H1": 32, "momentum60_H1":  46,
    "rsi2_H1":       58, "bb_extreme_H1": 43, "consensus":      60,
}

# Strategy max-hold in HOURS for age-decay TP tightening
STRAT_MAX_HOLD_HOURS = {
    "donchian20":    40 * 24, "momentum60":    60 * 24,
    "rsi2":          15 * 24, "3day_reverse":  10 * 24,
    "bb_extreme":    15 * 24, "donchian20_T":  40 * 24,
    "momentum60_T":  60 * 24, "consensus":     20 * 24,
    "donchian20_H1": 48,      "momentum60_H1": 48,
    "rsi2_H1":       24,      "bb_extreme_H1": 24,
}

# Live WR cache (read from analytics/by_strategy.csv, 5-min TTL)
_wr_cache = {"data": {}, "loaded_at": 0}
WR_CACHE_TTL_SEC = 300
BY_STRATEGY_CSV = os.path.join("analytics", "by_strategy.csv")


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


# ============================================================================
# TP distance cap — keeps targets achievable in reasonable time
# ============================================================================
_METALS = {"XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"}
_INDICES = {"US500", "US30", "UK100", "GER40", "JP225", "AUS200", "HK50", "FRA40"}


def _max_tp_pct_for(symbol):
    if symbol in _METALS:   return MAX_TP_PCT_METALS
    if symbol in _INDICES:  return MAX_TP_PCT_INDICES
    return MAX_TP_PCT_FX


def cap_tp_distance(plan):
    """If plan.tp is farther than the configured max % from entry, pull it in.
    SL is NOT touched — only TP. Returns the plan (possibly with new tp value)."""
    if not USE_MAX_TP_PCT or plan is None or plan.tp <= 0 or plan.entry_ref <= 0:
        return plan
    max_pct = _max_tp_pct_for(plan.symbol)
    max_dist = plan.entry_ref * max_pct / 100.0
    current_dist = abs(plan.tp - plan.entry_ref)
    if current_dist <= max_dist:
        return plan
    # Pull TP in to the cap
    if plan.direction == "BUY":
        plan.tp = plan.entry_ref + max_dist
    else:
        plan.tp = plan.entry_ref - max_dist
    return plan


# ============================================================================
# Live win-rate loader (cached)
# ============================================================================
def _load_live_wr():
    """Return dict mapping strategy_name -> {wr_pct, trades, edge_health}.
    Empty dict if file missing (cold start)."""
    now = time.time()
    if now - _wr_cache["loaded_at"] < WR_CACHE_TTL_SEC and _wr_cache["data"]:
        return _wr_cache["data"]
    data = {}
    if os.path.exists(BY_STRATEGY_CSV):
        try:
            with open(BY_STRATEGY_CSV, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    try:
                        data[row["strategy"]] = {
                            "wr": float(row.get("win_pct", 0) or 0),
                            "n":  int(row.get("trades", 0) or 0),
                        }
                    except (ValueError, KeyError):
                        continue
        except OSError:
            pass
    _wr_cache["data"] = data
    _wr_cache["loaded_at"] = now
    return data


# ============================================================================
# Score components — each returns 0-100
# ============================================================================
def _score_live_wr(strategy):
    """Compare live WR to backtest expectation.
       At/above backtest = 100. Half of backtest or worse = 0. Linear between.
       Cold start (fewer than 5 closed trades) = 80 (benefit of the doubt)."""
    expected = BACKTEST_EXPECTATION.get(strategy, 50)
    live = _load_live_wr().get(strategy)
    if not live or live["n"] < 5:
        return 80.0
    live_wr = live["wr"]
    half = expected * 0.5
    if live_wr >= expected:
        return 100.0
    if live_wr <= half:
        return 0.0
    return _clamp((live_wr - half) / (expected - half) * 100.0)


def _score_health(state, symbol, strategy):
    h = state.get("health", {})
    return 100.0 if health.is_active(h, symbol, strategy) else 0.0


def _score_spread(plan, sym_info):
    """spread / tp_distance ratio. < 5% = 100, > 15% = 0."""
    spread = float(sym_info.get("spread", 0))
    if plan.tp <= 0 or spread <= 0:
        return 100.0
    tp_dist = abs(plan.tp - plan.entry_ref)
    if tp_dist <= 0:
        return 0.0
    ratio = spread / tp_dist
    if ratio <= 0.05:
        return 100.0
    if ratio >= 0.15:
        return 0.0
    return _clamp((0.15 - ratio) / 0.10 * 100.0)


def _score_regime(bars):
    """ATR percentile over last 100 bars. 20-80 percentile = 100, extremes = 0."""
    if bars is None or len(bars) < 50:
        return 70.0   # not enough data, neutral score
    # Current ATR (last 14)
    cur = atr(bars[-15:])
    if cur is None or cur <= 0:
        return 50.0
    # Sample ATR at intervals across last 100 bars
    lookback = min(100, len(bars) - 15)
    samples = []
    step = max(1, lookback // 25)
    for end in range(len(bars) - lookback, len(bars), step):
        if end < 15: continue
        v = atr(bars[end-15:end])
        if v is not None and v > 0:
            samples.append(v)
    if not samples:
        return 70.0
    below = sum(1 for v in samples if v < cur)
    pct = 100.0 * below / len(samples)
    # 20-80 = full score, drops to 0 at <5 or >97
    if 20 <= pct <= 80:
        return 100.0
    if pct < 20:
        return _clamp((pct / 20.0) * 100.0)
    # pct > 80
    return _clamp((97.0 - pct) / 17.0 * 100.0) if pct < 97 else 0.0


def _score_mtf_alignment(plan, symbol):
    """Check higher-timeframe trend with EMA200 slope.
       D1 trades check H4 trend; H1 trades check D1 trend."""
    is_h1 = plan.strategy.endswith("_H1")
    htf = mt5.TIMEFRAME_D1 if is_h1 else mt5.TIMEFRAME_H4
    try:
        bars = mt5.copy_rates_from_pos(symbol, htf, 0, 220)
    except Exception:
        return 60.0
    if bars is None or len(bars) < 210:
        return 60.0
    closes = [float(b['close']) for b in bars[-201:-1]]   # last 200 closed
    if len(closes) < 200:
        return 60.0
    # Simple slope: EMA200 today vs EMA200 20 bars ago
    e_now = _ema(closes, 100)
    e_old = _ema(closes[:-20], 100)
    if e_now is None or e_old is None:
        return 60.0
    trend_up = e_now > e_old
    aligned = (trend_up and plan.direction == "BUY") or (not trend_up and plan.direction == "SELL")
    return 100.0 if aligned else 25.0   # not 0 — mean-rev strategies legitimately fade trends


def _score_concurrent_vote(plan, bars, sym_info, state):
    """How many OTHER base strategies on the same symbol fire same direction same bar?
       0 others = 0, 1 = 50, 2+ = 100."""
    if bars is None or len(bars) < 50:
        return 50.0
    # Sample a subset of base strategies (don't re-run the same one)
    base_to_check = ["donchian20", "momentum60", "rsi2", "3day_reverse", "bb_extreme"]
    agree = 0
    disagree = 0
    usd_per_pp = sym_info.get("usd_per_pp", 100.0)
    spread = sym_info.get("spread", 0.0)
    for base in base_to_check:
        if base == plan.strategy or base in plan.strategy:  # skip own + tight variants
            continue
        det = STRATEGY_DETECTORS.get(base)
        if det is None: continue
        try:
            other = det(bars, plan.symbol, usd_per_pp, spread)
        except Exception:
            continue
        if other is None: continue
        if other.direction == plan.direction:
            agree += 1
        else:
            disagree += 1
    if agree >= 2: return 100.0
    if agree == 1 and disagree == 0: return 70.0
    if agree == 1 and disagree >= 1: return 40.0
    if disagree >= 1: return 25.0
    return 50.0   # nobody agreed but nobody disagreed either


# ============================================================================
# Main quality scorer
# ============================================================================
WEIGHTS = {
    "live_wr":       25,
    "health":        15,
    "spread":        15,
    "regime":        15,
    "mtf_alignment": 15,
    "concurrent":    15,
}

def compute_quality_score(plan, bars, sym_info, state):
    """Return (final_score, breakdown_dict). final_score is 0-100."""
    breakdown = {
        "live_wr":       _score_live_wr(plan.strategy),
        "health":        _score_health(state, plan.symbol, plan.strategy),
        "spread":        _score_spread(plan, sym_info),
        "regime":        _score_regime(bars),
        "mtf_alignment": _score_mtf_alignment(plan, plan.symbol),
        "concurrent":    _score_concurrent_vote(plan, bars, sym_info, state),
    }
    total = sum(WEIGHTS[k] * breakdown[k] for k in WEIGHTS) / sum(WEIGHTS.values())
    return total, breakdown


# ============================================================================
# SL/TP migration on open positions
# ============================================================================
def _modify_position_sltp(position, new_sl, new_tp, reason):
    """Send TRADE_ACTION_SLTP modify request to broker."""
    info = mt5.symbol_info(position.symbol)
    digits = info.digits if info else 5
    req = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "symbol":   position.symbol,
        "position": position.ticket,
        "sl":       round(new_sl, digits),
        "tp":       round(new_tp, digits),
        "magic":    position.magic,
    }
    res = mt5.order_send(req)
    rc = getattr(res, "retcode", None)
    if rc == mt5.TRADE_RETCODE_DONE:
        print(f"[{reason}] #{position.ticket} {position.symbol} {position.comment} "
              f"SL→{round(new_sl, digits)} TP→{round(new_tp, digits)}")
        return True
    # Common: 10025 (no changes) - silent fail. 10027 (autotrading disabled) - log.
    if rc not in (10025, None):
        print(f"[{reason}-fail] #{position.ticket} retcode={rc}")
    return False


def _point_size(symbol):
    info = mt5.symbol_info(symbol)
    if info is None: return 0.0001
    return info.point or 0.0001


# ---------------------------------------------------------------------------
# SL-migration event log — feeds analytics/grade_sl_migration.py
# Each row records the moment a BE-move or age-decay TP-tightening fires,
# along with enough context to grade the outcome later.
# ---------------------------------------------------------------------------
_MIGRATION_LOG_DIR  = "logs"
_MIGRATION_LOG_FILE = os.path.join(_MIGRATION_LOG_DIR, "sl_migration_events.csv")
_MIGRATION_LOG_HEADER = [
    "ts", "ticket", "sym", "strat", "kind",
    "direction", "entry_price", "original_sl", "new_sl",
    "original_tp", "new_tp", "price_at_event",
    "favorable_pct_at_event",   # for BE move: ~50% at trigger; for age-decay: how deep into TP
    "age_hours",
]


def _log_migration_event(position, kind, original_sl, new_sl, original_tp, new_tp,
                          price_at_event, favorable_pct, age_hours):
    """Append one row to logs/sl_migration_events.csv. Best-effort — never raises."""
    try:
        os.makedirs(_MIGRATION_LOG_DIR, exist_ok=True)
        write_header = not os.path.exists(_MIGRATION_LOG_FILE)
        with open(_MIGRATION_LOG_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(_MIGRATION_LOG_HEADER)
            direction = "BUY" if position.type == mt5.POSITION_TYPE_BUY else "SELL"
            w.writerow([
                datetime.now(timezone.utc).isoformat(),
                position.ticket,
                position.symbol,
                (position.comment or "").strip(),
                kind,                          # "BE-move" or "age-decay"
                direction,
                f"{position.price_open:.5f}",
                f"{original_sl:.5f}",
                f"{new_sl:.5f}",
                f"{original_tp:.5f}",
                f"{new_tp:.5f}",
                f"{price_at_event:.5f}",
                f"{favorable_pct:.1f}",
                f"{age_hours:.2f}",
            ])
    except Exception as e:
        print(f"[sl-mig-log err] {e}")


def migrate_position_stops(positions):
    """For each open bot position, check BE-move and age-decay conditions.
       Returns total count of positions modified this tick."""
    if not USE_SL_MIGRATION or not positions:
        return 0
    n_modified = 0
    now_ts = datetime.now(timezone.utc).timestamp()
    for p in positions:
        if p.tp <= 0 or p.sl <= 0 or p.price_open <= 0:
            continue
        # Identify strategy from comment (most reliable)
        strat = (p.comment or "").strip()

        tick = mt5.symbol_info_tick(p.symbol)
        if tick is None:
            continue
        current = tick.bid if p.type == mt5.POSITION_TYPE_BUY else tick.ask

        tp_dist = abs(p.tp - p.price_open)
        if tp_dist <= 0:
            continue

        # ---- (A) BE move when ≥ 50% to TP ----
        if p.type == mt5.POSITION_TYPE_BUY:
            profit_dist = current - p.price_open
            already_at_or_past_be = p.sl >= p.price_open
            if not already_at_or_past_be and profit_dist >= SL_MIGRATION_TRIGGER * tp_dist:
                pt = _point_size(p.symbol)
                new_sl = p.price_open + SL_MIGRATION_BUFFER_PT * pt
                if new_sl > p.sl:
                    original_sl_snapshot = p.sl
                    if _modify_position_sltp(p, new_sl, p.tp, "BE-move"):
                        n_modified += 1
                        fav_pct = 100.0 * profit_dist / tp_dist
                        age_h = (now_ts - p.time) / 3600.0
                        _log_migration_event(p, "BE-move",
                                              original_sl_snapshot, new_sl,
                                              p.tp, p.tp, current,
                                              fav_pct, age_h)
                        continue   # don't also apply age-decay this tick
        else:  # SELL
            profit_dist = p.price_open - current
            already_at_or_past_be = p.sl <= p.price_open
            if not already_at_or_past_be and profit_dist >= SL_MIGRATION_TRIGGER * tp_dist:
                pt = _point_size(p.symbol)
                new_sl = p.price_open - SL_MIGRATION_BUFFER_PT * pt
                if new_sl < p.sl:
                    original_sl_snapshot = p.sl
                    if _modify_position_sltp(p, new_sl, p.tp, "BE-move"):
                        n_modified += 1
                        fav_pct = 100.0 * profit_dist / tp_dist
                        age_h = (now_ts - p.time) / 3600.0
                        _log_migration_event(p, "BE-move",
                                              original_sl_snapshot, new_sl,
                                              p.tp, p.tp, current,
                                              fav_pct, age_h)
                        continue

        # ---- (B) age-decay TP tightening ----
        max_hold_h = STRAT_MAX_HOLD_HOURS.get(strat, 24)
        age_h = (now_ts - p.time) / 3600.0
        if age_h < 0.7 * max_hold_h:
            continue
        # Only tighten if currently profitable
        if p.type == mt5.POSITION_TYPE_BUY:
            if current <= p.price_open:
                continue
            new_tp = (current + p.tp) / 2.0
            if new_tp < p.tp and new_tp > current:
                original_tp_snapshot = p.tp
                if _modify_position_sltp(p, p.sl, new_tp, "age-decay"):
                    n_modified += 1
                    fav_pct = 100.0 * (current - p.price_open) / tp_dist
                    _log_migration_event(p, "age-decay",
                                          p.sl, p.sl, original_tp_snapshot, new_tp,
                                          current, fav_pct, age_h)
        else:
            if current >= p.price_open:
                continue
            new_tp = (current + p.tp) / 2.0
            if new_tp > p.tp and new_tp < current:
                original_tp_snapshot = p.tp
                if _modify_position_sltp(p, p.sl, new_tp, "age-decay"):
                    n_modified += 1
                    fav_pct = 100.0 * (p.price_open - current) / tp_dist
                    _log_migration_event(p, "age-decay",
                                          p.sl, p.sl, original_tp_snapshot, new_tp,
                                          current, fav_pct, age_h)

    return n_modified
