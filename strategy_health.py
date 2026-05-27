"""
Strategy Health Tracker — automatic deactivation of decaying strategies.

Markets change. A strategy that worked for 8 years may stop working next month.
This module tracks each (symbol, strategy) combination's recent performance and
DEACTIVATES it if it shows clear degradation. Reactivates if performance recovers.

Storage: JSON file `strategy_health.json` in the project directory.
Updated each time a position closes (read from MT5 history).

Algorithm:
  - For each (symbol, strategy), maintain a rolling window of last N closed trades
  - Compute rolling win rate and average $ per trade
  - If WR drops below DEACTIVATE_WR_THRESHOLD over the last N trades → mark inactive
  - If subsequent paper-tracked trades show recovery → reactivate
  - Inactive combinations are SKIPPED by the bot during signal generation
"""

import json
import os
from datetime import datetime, timezone
from collections import deque
from typing import Optional

HEALTH_FILE = "strategy_health.json"

# Tunable thresholds
WINDOW_SIZE                 = 20      # last N trades to evaluate
MIN_TRADES_BEFORE_JUDGE     = 10      # don't deactivate before this many closed trades
DEACTIVATE_WR_THRESHOLD     = 35.0    # FALLBACK floor. Overridden per-strategy below.
DEACTIVATE_NET_LOSS_USD     = -100.0  # OR if net of last N trades is below this
REACTIVATE_WR_THRESHOLD     = 50.0    # paper-trade WR must recover above this
REACTIVATE_TRADES_NEEDED    = 5       # need this many paper-positive trades to come back

# Per-strategy deactivation floor — set to (BACKTEST_EXPECTATION × 0.7) so that
# a strategy is judged decayed only when its live WR falls materially below
# its known historical edge. Without this, donchian20 (BACKTEST_EXPECTATION=35%)
# would sit on the deactivation knife-edge and flip-flop. Sourced from
# trade_intelligence.BACKTEST_EXPECTATION (mirrored to avoid import cycle).
_BACKTEST_WR = {
    "donchian20":    35, "momentum60":    47, "rsi2":           60,
    "3day_reverse":  61, "bb_extreme":    42, "donchian20_T":   47,
    "momentum60_T":  55, "donchian20_H1": 32, "momentum60_H1":  46,
    "rsi2_H1":       58, "bb_extreme_H1": 43, "consensus":      60,
}


def _deactivate_wr_floor(strat):
    """The WR floor below which we deactivate this strategy. 70% of its
    backtest expectation — gives meaningful decay signal without false alarms
    on strategies that legitimately have low (but profitable) win rates."""
    expected = _BACKTEST_WR.get(strat)
    if expected is None:
        return DEACTIVATE_WR_THRESHOLD   # fallback
    return max(20.0, expected * 0.7)     # never below 20% — a true blowup


# ------------------------------------------------------------------
# Health store — dict keyed by "SYM|strategy"
# Each entry holds:
#   trades: list of recent closed trade outcomes [{ts, pnl, win}]
#   status: "active" | "inactive"
#   inactive_since: ISO timestamp
#   paper_trades: list of would-have-been trades while inactive (for reactivation)
# ------------------------------------------------------------------

def _key(sym, strat):
    return f"{sym}|{strat}"


def load_health():
    if not os.path.exists(HEALTH_FILE):
        return {}
    try:
        with open(HEALTH_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_health(data):
    try:
        with open(HEALTH_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception:
        pass


def record_closed_trade(data, sym, strat, pnl, ts=None):
    """Call when a real position closes. Updates rolling window and decides
    whether to deactivate the strategy."""
    k = _key(sym, strat)
    entry = data.setdefault(k, {"trades": [], "status": "active",
                                  "inactive_since": None, "paper_trades": [],
                                  "near_sl_touches": 0, "recoveries": 0})
    # Backfill counters on older records that pre-date this field
    entry.setdefault("near_sl_touches", 0)
    entry.setdefault("recoveries", 0)
    ts = ts or datetime.now(timezone.utc).isoformat()
    entry["trades"].append({"ts": ts, "pnl": pnl, "win": pnl > 0})
    # Keep only the last WINDOW_SIZE
    if len(entry["trades"]) > WINDOW_SIZE:
        entry["trades"] = entry["trades"][-WINDOW_SIZE:]
    # Evaluate
    if entry["status"] == "active" and len(entry["trades"]) >= MIN_TRADES_BEFORE_JUDGE:
        wins = sum(1 for t in entry["trades"] if t["win"])
        wr = 100.0 * wins / len(entry["trades"])
        net = sum(t["pnl"] for t in entry["trades"])
        wr_floor = _deactivate_wr_floor(strat)
        if wr < wr_floor or net < DEACTIVATE_NET_LOSS_USD:
            entry["status"] = "inactive"
            entry["inactive_since"] = ts
            entry["paper_trades"] = []
            print(f"[health] DEACTIVATED {sym} {strat} — "
                  f"WR={wr:.1f}% (floor {wr_floor:.0f}%), net=${net:+.2f}")
    return entry


def record_paper_trade(data, sym, strat, would_be_pnl):
    """While a combo is inactive, simulate its trades to see if it recovers."""
    k = _key(sym, strat)
    entry = data.get(k)
    if not entry or entry["status"] != "inactive":
        return
    entry["paper_trades"].append({"pnl": would_be_pnl, "win": would_be_pnl > 0})
    if len(entry["paper_trades"]) > WINDOW_SIZE:
        entry["paper_trades"] = entry["paper_trades"][-WINDOW_SIZE:]
    if len(entry["paper_trades"]) >= REACTIVATE_TRADES_NEEDED:
        wins = sum(1 for t in entry["paper_trades"] if t["win"])
        wr = 100.0 * wins / len(entry["paper_trades"])
        if wr >= REACTIVATE_WR_THRESHOLD:
            entry["status"] = "active"
            entry["inactive_since"] = None
            entry["paper_trades"] = []
            entry["trades"] = []  # reset rolling window for fresh start
            print(f"[health] REACTIVATED {sym} {strat} — paper WR recovered to {wr:.1f}%")


def record_near_sl_touch(data, sym, strat):
    """Called once per ticket when it first crosses 70% of SL distance.
    De-dup is the caller's responsibility (track which tickets already counted)."""
    k = _key(sym, strat)
    entry = data.setdefault(k, {"trades": [], "status": "active",
                                  "inactive_since": None, "paper_trades": [],
                                  "near_sl_touches": 0, "recoveries": 0})
    entry["near_sl_touches"] = entry.get("near_sl_touches", 0) + 1


def record_recovery_outcome(data, sym, strat, recovered):
    """Called when a ticket that previously touched 70%-SL finally closes.
    `recovered` = True if it closed profitably OR consumed_pct dropped back below 50%."""
    k = _key(sym, strat)
    entry = data.get(k)
    if entry is None:
        return
    if recovered:
        entry["recoveries"] = entry.get("recoveries", 0) + 1


def recovery_rate(data, sym, strat, min_samples=10):
    """Return live recovery rate (0.0-1.0) for this combo, or None if too few samples."""
    k = _key(sym, strat)
    entry = data.get(k)
    if entry is None:
        return None
    n = entry.get("near_sl_touches", 0)
    if n < min_samples:
        return None
    return entry.get("recoveries", 0) / n


def is_active(data, sym, strat):
    """Return True if this combination is currently allowed to trade."""
    k = _key(sym, strat)
    entry = data.get(k)
    if entry is None:
        return True   # never seen → assume active
    return entry["status"] == "active"


def summary(data):
    """Return human-readable summary of all combinations."""
    lines = []
    for k, entry in sorted(data.items()):
        if not entry["trades"] and not entry["paper_trades"]:
            continue
        if entry["trades"]:
            wins = sum(1 for t in entry["trades"] if t["win"])
            wr = 100.0 * wins / len(entry["trades"])
            net = sum(t["pnl"] for t in entry["trades"])
            tag = "✅" if entry["status"] == "active" else "❌"
            lines.append(f"  {tag} {k:<25} trades={len(entry['trades']):2d}  "
                         f"WR={wr:5.1f}%  net=${net:+.2f}  status={entry['status']}")
    return "\n".join(lines)
