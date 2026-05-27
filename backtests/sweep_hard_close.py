"""Sweep RESCUE_HARD_CLOSE_PCT to find the empirical optimum.

Reuses the same per-trade max_consumed_pct data from backtest_rescue_recovery,
just tries different hard-close thresholds and reports RESCUE total at each."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict
import MetaTrader5 as mt5
import numpy as np

# Reuse the same setup as the main backtest
exec(open(os.path.join(os.path.dirname(__file__), "backtest_rescue_recovery.py")).read()
     .split("# ---------------------------------------------------------------------------\n# Run everything")[0]
     .replace("mt5.shutdown()", ""))

# Re-run trade generation (same code path as main backtest)
all_trades = defaultdict(list)
for sym in SYMBOLS:
    if sym not in SYM: continue
    bars = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 2000)
    if bars is None or len(bars) < 250: continue
    for name, fn in STRATS:
        try:
            for t in fn(bars, sym):
                all_trades[name].append((sym, *t))
        except Exception:
            pass

# Sweep
print("\n" + "=" * 70)
print(f"{'HARD_CLOSE %':>14} {'RESCUE $':>14} {'vs LEGACY':>14} {'vs NATURAL':>14}")
print("=" * 70)

# Compute LEGACY and NATURAL once
def policies(trades, hard_close_pct):
    if not trades:
        return 0, 0, 0
    pnls = np.array([t[3] for t in trades])
    outcomes = [t[4] for t in trades]
    mc = np.array([t[5] for t in trades])
    touched = mc >= NEAR_SL_PCT
    R = abs(np.mean([t[3] for t in trades if t[4] == "SL"])) if any(t[4] == "SL" for t in trades) else 50.0
    untouched_pnl = float(pnls[~touched].sum())
    legacy = untouched_pnl + (-0.7 * R * int(touched.sum()))
    natural = float(pnls.sum())
    hard = mc >= hard_close_pct
    soft = touched & ~hard
    rescue = untouched_pnl + float(pnls[soft].sum()) + (-0.9 * R * int(hard.sum()))
    return natural, legacy, rescue

for hc in [0.85, 0.90, 0.92, 0.95, 0.97, 0.99, 1.01]:   # 1.01 = effectively no hard-close
    total_nat = total_leg = total_resc = 0
    for name, _ in STRATS:
        nat, leg, resc = policies(all_trades[name], hc)
        total_nat += nat
        total_leg += leg
        total_resc += resc
    label = "no hard-close" if hc > 1.0 else f"{hc*100:.0f}%"
    print(f"{label:>14} {total_resc:>+14,.2f} {total_resc-total_leg:>+14,.2f} "
          f"{total_resc-total_nat:>+14,.2f}")

print(f"\n  LEGACY total:  ${total_leg:+,.2f}")
print(f"  NATURAL total: ${total_nat:+,.2f}")
mt5.shutdown()
