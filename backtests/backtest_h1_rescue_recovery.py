"""
H1 strategies — rescue layer recovery-rate measurement.

Same methodology as backtest_rescue_recovery.py but on H1 bars and using
the four H1 strategies that have measured edge in backtest_v12:
  donchian20_H1, momentum60_H1, rsi2_H1, bb_extreme_H1.

H1 has more bars per year (~6000) but trades are usually shorter-lived.
Measured recovery rates may differ from D1 because intraday whipsaws are
more common.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict
import MetaTrader5 as mt5
import numpy as np

SYMBOLS = [
    "XAUUSD", "XAGUSD", "US500", "US30",
    "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "AUDJPY", "EURJPY",
    "AUDUSD", "NZDUSD", "EURGBP", "CADJPY", "CHFJPY", "NZDJPY",
    "EURCHF", "EURAUD", "GBPAUD",
]
RISK_PER_TRADE_USD = 50.0
MIN_LOT = 0.01; MAX_LOT = 100.0; LOT_STEP = 0.01

NEAR_SL_PCT = 0.70
HARD_CLOSE_PCT = 0.99   # use the calibrated optimum from the D1 sweep

print(">>> connecting to MT5...")
if not mt5.initialize():
    print("MT5 init failed:", mt5.last_error())
    sys.exit(1)

SYM = {}
for s in SYMBOLS:
    mt5.symbol_select(s, True)
    info = mt5.symbol_info(s)
    if info is None:
        continue
    pip = 10 * info.trade_tick_size
    upp = (info.trade_tick_value / info.trade_tick_size
           if info.trade_tick_size > 0 else info.trade_contract_size)
    if s == "XAUUSD":         sp = 0.30
    elif s == "XAGUSD":       sp = 0.03
    elif s in ("US30", "US500"): sp = 0.50
    else:                     sp = 1.5 * pip
    SYM[s] = dict(pip=pip, spread=sp, usd_per_pp=upp, digits=info.digits)


def normalize_lot(v):
    v = max(MIN_LOT, min(MAX_LOT, v))
    return round(round(v / LOT_STEP) * LOT_STEP, 2)


def size_for_risk(d, upp):
    if d <= 0 or upp <= 0:
        return 0
    return normalize_lot(RISK_PER_TRADE_USD / (d * upp))


from indicators import atr_at as atr, rsi_at, sma_at as sma


def sim_trade(bars, ei, dirn, entry, sl, tp, lot, upp, sp, max_bars):
    sl_distance = abs(entry - sl)
    if sl_distance <= 0:
        return ei, 0.0, "SKIP", 0.0
    max_consumed = 0.0
    j = ei + 1
    end = min(len(bars), ei + max_bars + 1)
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


# H1 strategies — mirror live detectors (loser_rescue.py refers to them by these names)
def strat_donchian20_H1(bars, sym):
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
        sld = 2.0 * a; tpd = 3.0 * sld
        sl = entry - sld if sig == "BUY" else entry + sld
        tp = entry + tpd if sig == "BUY" else entry - tpd
        lot = size_for_risk(sld, upp)
        if lot < MIN_LOT: continue
        _, pnl, o, mc = sim_trade(bars, i+1, sig, entry, sl, tp, lot, upp, sp, 48)
        out.append((int(cur['time']), sig, pnl, o, mc))
        last = i
    return out


def strat_momentum60_H1(bars, sym):
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
        sld = 2.0 * a; tpd = 1.5 * sld
        sl = entry - sld if sig == "BUY" else entry + sld
        tp = entry + tpd if sig == "BUY" else entry - tpd
        lot = size_for_risk(sld, upp)
        if lot < MIN_LOT: continue
        _, pnl, o, mc = sim_trade(bars, i+1, sig, entry, sl, tp, lot, upp, sp, 48)
        out.append((int(bars[i]['time']), sig, pnl, o, mc))
        last = i
    return out


def strat_rsi2_H1(bars, sym):
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
        _, pnl, o, mc = sim_trade(bars, i+1, sig, entry, sl, tp, lot, upp, sp, 24)
        out.append((int(bars[i]['time']), sig, pnl, o, mc))
        last = i
    return out


def strat_bb_extreme_H1(bars, sym):
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
        _, pnl, o, mc = sim_trade(bars, i+1, sig, entry, sl, tp, lot, upp, sp, 24)
        out.append((int(bars[i]['time']), sig, pnl, o, mc))
        last = i
    return out


STRATS = [
    ("donchian20_H1", strat_donchian20_H1),
    ("momentum60_H1", strat_momentum60_H1),
    ("rsi2_H1",       strat_rsi2_H1),
    ("bb_extreme_H1", strat_bb_extreme_H1),
]

print("\nFetching H1 bars and running strategies...")
all_trades = defaultdict(list)
for sym in SYMBOLS:
    if sym not in SYM: continue
    # H1 bars — request up to 10000 (~1.5yr of trading hours per symbol)
    bars = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 10000)
    if bars is None or len(bars) < 500:
        print(f"  {sym}: insufficient bars")
        continue
    hours = (bars[-1]['time'] - bars[0]['time']) / 3600
    for name, fn in STRATS:
        try:
            for t in fn(bars, sym):
                all_trades[name].append((sym, *t))
        except Exception as e:
            print(f"  {sym} {name}: ERROR {e}")
    print(f"  {sym}: H1 history={int(hours/24)}d  "
          + ", ".join(f"{name}={sum(1 for x in all_trades[name] if x[0]==sym)}" for name, _ in STRATS))


def analyze(name, trades, hard_close_pct):
    if not trades:
        return None
    n = len(trades)
    pnls = np.array([t[3] for t in trades])
    outcomes = [t[4] for t in trades]
    mc = np.array([t[5] for t in trades])
    touched = mc >= NEAR_SL_PCT
    n_touched = int(touched.sum())
    if n_touched > 0:
        touched_outcomes = [outcomes[i] for i in range(n) if touched[i]]
        n_sl = sum(1 for o in touched_outcomes if o == "SL")
        n_tp = sum(1 for o in touched_outcomes if o == "TP")
        n_to = sum(1 for o in touched_outcomes if o == "TIMEOUT")
        n_recov = int((pnls[touched] > 0).sum())
        recovery_rate = n_recov / n_touched
    else:
        n_sl = n_tp = n_to = n_recov = 0
        recovery_rate = 0.0
    R = abs(np.mean([t[3] for t in trades if t[4] == "SL"])) if any(t[4] == "SL" for t in trades) else RISK_PER_TRADE_USD
    untouched_pnl = float(pnls[~touched].sum())
    natural_total = float(pnls.sum())
    legacy_total = untouched_pnl + (-0.7 * R * n_touched)
    hard = mc >= hard_close_pct
    soft = touched & ~hard
    n_hard = int(hard.sum())
    rescue_total = untouched_pnl + float(pnls[soft].sum()) + (-0.9 * R * n_hard)
    return dict(name=name, n=n, n_touched=n_touched, n_hard=n_hard,
                recovery_rate=recovery_rate, n_sl=n_sl, n_tp=n_tp, n_to=n_to, n_recov=n_recov,
                avg_R=R, natural_total=natural_total, legacy_total=legacy_total, rescue_total=rescue_total)


results = [analyze(name, all_trades[name], HARD_CLOSE_PCT) for name, _ in STRATS]
results = [r for r in results if r is not None]

print("\n" + "=" * 110)
print(f"H1 RESCUE-LAYER RECOVERY ANALYSIS  —  risk ${RISK_PER_TRADE_USD}/trade  —  "
      f"NEAR_SL={NEAR_SL_PCT*100:.0f}%, HARD_CLOSE={HARD_CLOSE_PCT*100:.0f}%")
print("=" * 110)
hdr = (f"{'Strategy':<16} {'N':>5} {'Touched':>8} {'Recov%':>7} "
       f"{'Avg-R$':>8}  {'NATURAL$':>12} {'LEGACY$':>12} {'RESCUE$':>12}  {'R-vs-L':>10}")
print(hdr)
print("-" * len(hdr))
totals = {"natural": 0.0, "legacy": 0.0, "rescue": 0.0, "n": 0, "touched": 0, "recov": 0}
for r in results:
    diff = r['rescue_total'] - r['legacy_total']
    print(f"{r['name']:<16} {r['n']:>5} {r['n_touched']:>8} {r['recovery_rate']*100:>6.1f}% "
          f"{r['avg_R']:>8.2f}  {r['natural_total']:>+12.2f} {r['legacy_total']:>+12.2f} "
          f"{r['rescue_total']:>+12.2f}  {diff:>+10.2f}")
    totals["natural"] += r['natural_total']
    totals["legacy"]  += r['legacy_total']
    totals["rescue"]  += r['rescue_total']
    totals["n"]       += r['n']
    totals["touched"] += r['n_touched']
    totals["recov"]   += r['n_recov']
print("-" * len(hdr))
agg = (100.0 * totals["recov"] / totals["touched"]) if totals["touched"] else 0.0
print(f"{'TOTAL':<16} {totals['n']:>5} {totals['touched']:>8} {agg:>6.1f}% "
      f"{'':>8}  {totals['natural']:>+12.2f} {totals['legacy']:>+12.2f} "
      f"{totals['rescue']:>+12.2f}  {totals['rescue']-totals['legacy']:>+10.2f}")

print("\nPer-strategy recovery rate (for loser_rescue.py _MEASURED_RECOVERY update):")
for r in results:
    print(f"    \"{r['name']}\": {r['recovery_rate']:.3f},")

mt5.shutdown()
