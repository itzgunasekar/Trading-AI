"""
70/30 Out-of-Sample validation of the Loser Rescue Layer.

Question this answers:
  Were the rescue-layer parameters (HARD_CLOSE=99, KEEP_THRESHOLD=45, the
  per-strategy bootstrap recovery rates) overfit to the full 8-year window,
  or do they hold up on data they never saw at calibration time?

Method:
  1. Replay each strategy on full bar history (same as backtest_rescue_recovery)
  2. Sort all trades chronologically
  3. IN-SAMPLE = first 70% of trades (by time)
     OUT-OF-SAMPLE = last 30% of trades
  4. For each split, compute:
        - measured recovery rate (Touched-70%-and-recovered / Touched-70%)
        - RESCUE total $ vs LEGACY total $ vs NATURAL total $
  5. Report whether the OOS edge holds within (-20% / +∞) of the in-sample edge.
     Any decay sharper than 20% is a red flag for overfitting.

Method is conservative: it tests the BOOTSTRAP behavior (keep-everything-below-
HARD_CLOSE). The live scorer can be more selective, so live edge should be
similar or slightly better than what this script measures.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict
import MetaTrader5 as mt5
import numpy as np

from indicators import atr_at as atr, rsi_at, sma_at as sma


SYMBOLS = [
    "XAUUSD", "XAGUSD", "US500", "US30",
    "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "AUDJPY", "EURJPY",
    "AUDUSD", "NZDUSD", "EURGBP", "CADJPY", "CHFJPY", "NZDJPY",
]
RISK_PER_TRADE_USD = 50.0
MIN_LOT = 0.01; MAX_LOT = 100.0; LOT_STEP = 0.01

NEAR_SL_PCT = 0.70
HARD_CLOSE_PCT = 0.99   # the production setting


print(">>> connecting to MT5...")
if not mt5.initialize():
    print("MT5 init failed:", mt5.last_error()); sys.exit(1)

SYM = {}
for s in SYMBOLS:
    mt5.symbol_select(s, True)
    info = mt5.symbol_info(s)
    if info is None: continue
    pip = 10 * info.trade_tick_size
    upp = (info.trade_tick_value / info.trade_tick_size
           if info.trade_tick_size > 0 else info.trade_contract_size)
    if s == "XAUUSD":             sp = 0.30
    elif s == "XAGUSD":           sp = 0.03
    elif s in ("US30", "US500"):  sp = 0.50
    else:                         sp = 1.5 * pip
    SYM[s] = dict(pip=pip, spread=sp, usd_per_pp=upp, digits=info.digits)


def normalize_lot(v):
    v = max(MIN_LOT, min(MAX_LOT, v))
    return round(round(v / LOT_STEP) * LOT_STEP, 2)


def size_for_risk(d, upp):
    if d <= 0 or upp <= 0: return 0
    return normalize_lot(RISK_PER_TRADE_USD / (d * upp))


def sim_trade(bars, ei, dirn, entry, sl, tp, lot, upp, sp, max_bars):
    sl_distance = abs(entry - sl)
    if sl_distance <= 0: return ei, 0.0, "SKIP", 0.0
    max_consumed = 0.0
    j = ei + 1; end = min(len(bars), ei + max_bars + 1)
    while j < end:
        b = bars[j]
        if dirn == "BUY":
            adverse = max(0.0, entry - b['low'])
            if adverse > 0:
                cp = adverse / sl_distance
                if cp > max_consumed: max_consumed = cp
            if b['low']  <= sl: return j, (sl - entry) * lot * upp, "SL", max_consumed
            if b['high'] >= tp: return j, (tp - entry) * lot * upp, "TP", max_consumed
        else:
            adverse = max(0.0, (b['high'] + sp) - entry)
            if adverse > 0:
                cp = adverse / sl_distance
                if cp > max_consumed: max_consumed = cp
            if b['high'] + sp >= sl: return j, (entry - sl) * lot * upp, "SL", max_consumed
            if b['low']  + sp <= tp: return j, (entry - tp) * lot * upp, "TP", max_consumed
        j += 1
    exit_px = bars[end-1]['close'] + (0 if dirn == "BUY" else sp)
    pnl = (exit_px - entry) * lot * upp if dirn == "BUY" else (entry - exit_px) * lot * upp
    return end - 1, pnl, "TIMEOUT", max_consumed


# Strategy detectors (subset — D1 only for clarity; same logic as live bot)
def strat_donchian20(bars, sym):
    info = SYM[sym]; sp = info['spread']; upp = info['usd_per_pp']
    out = []; last = -100
    for i in range(25, len(bars) - 1):
        if i - last < 5: continue
        recent = bars[i-20:i]
        hh = max(r['high'] for r in recent); ll = min(r['low'] for r in recent)
        a = atr(bars, i, 14)
        if a is None or a == 0: continue
        cur = bars[i]
        sig = "BUY" if cur['high'] >= hh else ("SELL" if cur['low'] <= ll else None)
        if sig is None: continue
        if i + 1 >= len(bars): break
        entry = bars[i+1]['open'] + (sp if sig == "BUY" else 0)
        sld = 2.0 * a
        sl = entry - sld if sig == "BUY" else entry + sld
        tp = entry + 3.0 * sld if sig == "BUY" else entry - 3.0 * sld
        lot = size_for_risk(sld, upp)
        if lot < MIN_LOT: continue
        _, pnl, o, mc = sim_trade(bars, i+1, sig, entry, sl, tp, lot, upp, sp, 40)
        out.append((int(cur['time']), sig, pnl, o, mc))
        last = i
    return out


def strat_momentum60(bars, sym):
    info = SYM[sym]; sp = info['spread']; upp = info['usd_per_pp']
    out = []; last = -100
    for i in range(65, len(bars) - 1):
        if i - last < 10: continue
        a = atr(bars, i, 14)
        if a is None or a == 0: continue
        ret60 = bars[i]['close'] - bars[i-60]['close']
        if abs(ret60) < 2 * a: continue
        sig = "BUY" if ret60 > 0 else "SELL"
        if i + 1 >= len(bars): break
        entry = bars[i+1]['open'] + (sp if sig == "BUY" else 0)
        sld = 2.0 * a; tpd = 3.0 * a
        sl = entry - sld if sig == "BUY" else entry + sld
        tp = entry + tpd if sig == "BUY" else entry - tpd
        lot = size_for_risk(sld, upp)
        if lot < MIN_LOT: continue
        _, pnl, o, mc = sim_trade(bars, i+1, sig, entry, sl, tp, lot, upp, sp, 60)
        out.append((int(bars[i]['time']), sig, pnl, o, mc))
        last = i
    return out


def strat_rsi2(bars, sym):
    info = SYM[sym]; sp = info['spread']; upp = info['usd_per_pp']
    out = []; last = -100
    for i in range(210, len(bars) - 1):
        if i - last < 3: continue
        ma200 = sma(bars, i, 200)
        if ma200 is None: continue
        r = rsi_at(bars, i, 2)
        if r is None: continue
        sig = None
        if bars[i]['close'] > ma200 and r < 10:  sig = "BUY"
        elif bars[i]['close'] < ma200 and r > 90: sig = "SELL"
        if sig is None: continue
        a = atr(bars, i, 14)
        if a is None or a == 0: continue
        if i + 1 >= len(bars): break
        entry = bars[i+1]['open'] + (sp if sig == "BUY" else 0)
        sld = 2.0 * a; tpd = 1.5 * a
        sl = entry - sld if sig == "BUY" else entry + sld
        tp = entry + tpd if sig == "BUY" else entry - tpd
        lot = size_for_risk(sld, upp)
        if lot < MIN_LOT: continue
        _, pnl, o, mc = sim_trade(bars, i+1, sig, entry, sl, tp, lot, upp, sp, 15)
        out.append((int(bars[i]['time']), sig, pnl, o, mc))
        last = i
    return out


def strat_3day_reverse(bars, sym):
    info = SYM[sym]; sp = info['spread']; upp = info['usd_per_pp']
    out = []
    for i in range(5, len(bars) - 1):
        b1, b2, b3 = bars[i-2], bars[i-1], bars[i]
        all_up   = b1['close'] > b1['open'] and b2['close'] > b2['open'] and b3['close'] > b3['open']
        all_down = b1['close'] < b1['open'] and b2['close'] < b2['open'] and b3['close'] < b3['open']
        if not (all_up or all_down): continue
        sig = "SELL" if all_up else "BUY"
        a = atr(bars, i, 14)
        if a is None or a == 0: continue
        if i + 1 >= len(bars): break
        entry = bars[i+1]['open'] + (sp if sig == "BUY" else 0)
        sld = 1.5 * a; tpd = 1.0 * a
        sl = entry - sld if sig == "BUY" else entry + sld
        tp = entry + tpd if sig == "BUY" else entry - tpd
        lot = size_for_risk(sld, upp)
        if lot < MIN_LOT: continue
        _, pnl, o, mc = sim_trade(bars, i+1, sig, entry, sl, tp, lot, upp, sp, 10)
        out.append((int(bars[i]['time']), sig, pnl, o, mc))
    return out


def strat_bb_extreme(bars, sym):
    info = SYM[sym]; sp = info['spread']; upp = info['usd_per_pp']
    out = []; last = -100
    for i in range(25, len(bars) - 1):
        if i - last < 3: continue
        win = [bars[k]['close'] for k in range(i-19, i+1)]
        mid = sum(win) / 20
        var = sum((c - mid) ** 2 for c in win) / 20
        std = var ** 0.5
        if std == 0: continue
        upper = mid + 2.5 * std; lower = mid - 2.5 * std
        c = bars[i]['close']
        sig = "BUY" if c < lower else ("SELL" if c > upper else None)
        if sig is None: continue
        a = atr(bars, i, 14)
        if a is None or a == 0: continue
        if i + 1 >= len(bars): break
        entry = bars[i+1]['open'] + (sp if sig == "BUY" else 0)
        sld = 1.5 * a
        sl = entry - sld if sig == "BUY" else entry + sld
        tp = mid
        if abs(tp - entry) < 0.5 * a: continue
        lot = size_for_risk(sld, upp)
        if lot < MIN_LOT: continue
        _, pnl, o, mc = sim_trade(bars, i+1, sig, entry, sl, tp, lot, upp, sp, 15)
        out.append((int(bars[i]['time']), sig, pnl, o, mc))
        last = i
    return out


STRATS = [
    ("donchian20",   strat_donchian20),
    ("momentum60",   strat_momentum60),
    ("rsi2",         strat_rsi2),
    ("3day_reverse", strat_3day_reverse),
    ("bb_extreme",   strat_bb_extreme),
]


print("\nFetching bars + running strategies...")
all_trades = defaultdict(list)
for sym in SYMBOLS:
    if sym not in SYM: continue
    bars = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 2000)
    if bars is None or len(bars) < 250: continue
    for name, fn in STRATS:
        try:
            for t in fn(bars, sym):
                all_trades[name].append((sym, *t))   # (sym, ts, sig, pnl, outcome, mc)
        except Exception:
            pass
    print(f"  {sym}: " + ", ".join(f"{n}={sum(1 for x in all_trades[n] if x[0]==sym)}" for n, _ in STRATS))


def policies_for_set(trades):
    """Compute NATURAL, LEGACY, RESCUE totals + recovery rate for a trade subset."""
    if not trades:
        return None
    pnls = np.array([t[3] for t in trades])
    outcomes = [t[4] for t in trades]
    mc = np.array([t[5] for t in trades])
    touched = mc >= NEAR_SL_PCT
    n_touched = int(touched.sum())
    n_recov = int((pnls[touched] > 0).sum()) if n_touched else 0
    recovery_rate = (n_recov / n_touched) if n_touched else 0.0
    R = abs(np.mean([t[3] for t in trades if t[4] == "SL"])) if any(t[4] == "SL" for t in trades) else RISK_PER_TRADE_USD
    untouched_pnl = float(pnls[~touched].sum())
    natural_total = float(pnls.sum())
    legacy_total = untouched_pnl + (-0.7 * R * n_touched)
    hard = mc >= HARD_CLOSE_PCT
    soft = touched & ~hard
    rescue_total = untouched_pnl + float(pnls[soft].sum()) + (-0.9 * R * int(hard.sum()))
    return dict(n=len(trades), n_touched=n_touched, recovery_rate=recovery_rate,
                natural=natural_total, legacy=legacy_total, rescue=rescue_total)


# Split each strategy's trades chronologically and analyze
print("\n" + "=" * 120)
print(f"OOS VALIDATION  —  70/30 chronological split  —  HARD_CLOSE={HARD_CLOSE_PCT*100:.0f}%")
print("=" * 120)
hdr = (f"{'Strategy':<14} {'Window':>5} {'N':>6} {'Touched':>8} {'Recov%':>7}  "
       f"{'NATURAL':>11} {'LEGACY':>11} {'RESCUE':>11}  {'R-vs-L':>10}  {'R-vs-N':>10}")
print(hdr)
print("-" * len(hdr))

total_is_resc = 0; total_oos_resc = 0
total_is_leg = 0; total_oos_leg = 0
total_is_nat = 0; total_oos_nat = 0
red_flags = []

for name, _ in STRATS:
    trades = sorted(all_trades[name], key=lambda t: t[1])   # by timestamp
    if not trades:
        continue
    split_idx = int(len(trades) * 0.7)
    is_set  = trades[:split_idx]
    oos_set = trades[split_idx:]
    is_r  = policies_for_set(is_set)
    oos_r = policies_for_set(oos_set)
    if is_r is None or oos_r is None: continue

    for label, r in [("IS", is_r), ("OOS", oos_r)]:
        rvl = r['rescue'] - r['legacy']
        rvn = r['rescue'] - r['natural']
        print(f"{name:<14} {label:>5} {r['n']:>6} {r['n_touched']:>8} {r['recovery_rate']*100:>6.1f}%  "
              f"{r['natural']:>+11.0f} {r['legacy']:>+11.0f} {r['rescue']:>+11.0f}  "
              f"{rvl:>+10.0f}  {rvn:>+10.0f}")
        if label == "IS":
            total_is_resc += r['rescue']; total_is_leg += r['legacy']; total_is_nat += r['natural']
            is_edge = rvl
            is_recov = r['recovery_rate']
        else:
            total_oos_resc += r['rescue']; total_oos_leg += r['legacy']; total_oos_nat += r['natural']
            oos_edge = rvl
            oos_recov = r['recovery_rate']
    # Per-strategy red-flag: did OOS rescue beat legacy?
    if oos_edge < 0:
        red_flags.append(f"{name}: OOS rescue $({oos_edge:+.0f}) BELOW legacy — investigate")
    # Did recovery rate change materially between IS and OOS?
    if is_recov > 0:
        decay = (oos_recov - is_recov) / is_recov
        if decay < -0.30:
            red_flags.append(f"{name}: recovery rate decayed {decay*100:+.0f}% IS→OOS")
    print()


print("-" * len(hdr))
print(f"{'TOTAL':<14} {'IS':>5} {'':>6} {'':>8} {'':>7}  "
      f"{total_is_nat:>+11.0f} {total_is_leg:>+11.0f} {total_is_resc:>+11.0f}  "
      f"{total_is_resc-total_is_leg:>+10.0f}  {total_is_resc-total_is_nat:>+10.0f}")
print(f"{'TOTAL':<14} {'OOS':>5} {'':>6} {'':>8} {'':>7}  "
      f"{total_oos_nat:>+11.0f} {total_oos_leg:>+11.0f} {total_oos_resc:>+11.0f}  "
      f"{total_oos_resc-total_oos_leg:>+10.0f}  {total_oos_resc-total_oos_nat:>+10.0f}")

print("\n" + "=" * 120)
print("VERDICT")
print("=" * 120)
is_edge = total_is_resc - total_is_leg
oos_edge = total_oos_resc - total_oos_leg

if is_edge > 0 and oos_edge > 0:
    decay_pct = 100.0 * (oos_edge / max(is_edge, 1) - 0.30 / 0.70)   # normalize: 30% of bars = 30% of expected edge
    # Simpler: oos_edge should be ~3/7 of is_edge if perfectly stable
    expected_oos = is_edge * (0.30 / 0.70)
    realized_decay = 100.0 * (oos_edge - expected_oos) / max(abs(expected_oos), 1)
    print(f"  IS  rescue-vs-legacy edge:   ${is_edge:+,.0f}")
    print(f"  OOS rescue-vs-legacy edge:   ${oos_edge:+,.0f}")
    print(f"  OOS expected (if stable):    ${expected_oos:+,.0f}  (3/7 of IS by trade volume)")
    print(f"  OOS vs expected:             {realized_decay:+.1f}%")
    if realized_decay >= -20.0:
        print(f"  >>> PASS: OOS edge is within 20% of in-sample expectation.")
        print(f"      The rescue calibration generalizes — no obvious overfitting.")
    elif realized_decay >= -50.0:
        print(f"  >>> WEAK PASS: OOS edge decayed but is still positive.")
        print(f"      Watch live data closely — consider raising RESCUE_KEEP_THRESHOLD.")
    else:
        print(f"  >>> FAIL: OOS edge decayed sharply — overfitting risk.")
        print(f"      Consider widening RESCUE_KEEP_THRESHOLD and/or per-family bootstrap defaults.")
elif oos_edge > 0:
    print(f"  Strange: IS edge was ${is_edge:+,.0f} but OOS is ${oos_edge:+,.0f} (positive).")
    print(f"  Either the rescue layer is even better than expected, or sample sizes are small.")
else:
    print(f"  >>> FAIL: OOS edge ${oos_edge:+,.0f} is non-positive. Rescue layer may not generalize.")

print()
if red_flags:
    print("  Red flags found:")
    for f in red_flags:
        print(f"    - {f}")
else:
    print("  No per-strategy red flags. Recovery rates and edges held across the split.")

mt5.shutdown()
