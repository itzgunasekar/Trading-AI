"""
Tight-TP variants and consensus strategy — rescue layer recovery measurement.

These differ from the base strategies in R:R ratio:
  donchian20_T:  SL=2×ATR, TP=1.5×SL  (vs 3×SL original) — break-even ~12%
  momentum60_T:  SL=2×ATR, TP=1.0×SL  (vs 1.5×SL original) — break-even ~15%
  consensus:     SL=1.75×ATR, TP=1.5×ATR — break-even ~17%

Tighter TPs → higher break-even recovery → potentially less rescue edge.
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
]
RISK_PER_TRADE_USD = 50.0
MIN_LOT = 0.01; MAX_LOT = 100.0; LOT_STEP = 0.01

NEAR_SL_PCT = 0.70
HARD_CLOSE_PCT = 0.99

print(">>> connecting to MT5...")
if not mt5.initialize():
    print("MT5 init failed:", mt5.last_error())
    sys.exit(1)

SYM = {}
for s in SYMBOLS:
    mt5.symbol_select(s, True)
    info = mt5.symbol_info(s)
    if info is None: continue
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
    if d <= 0 or upp <= 0: return 0
    return normalize_lot(RISK_PER_TRADE_USD / (d * upp))


from indicators import atr_at as atr, rsi_at, sma_at as sma


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


# Tight-TP variants
def strat_donchian20_T(bars, sym):
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
        sld = 2.0 * a; tpd = 1.5 * sld   # tight: 1.5×SL
        sl = entry - sld if sig == "BUY" else entry + sld
        tp = entry + tpd if sig == "BUY" else entry - tpd
        lot = size_for_risk(sld, upp)
        if lot < MIN_LOT: continue
        _, pnl, o, mc = sim_trade(bars, i+1, sig, entry, sl, tp, lot, upp, sp, 40)
        out.append((int(cur['time']), sig, pnl, o, mc))
        last = i
    return out


def strat_momentum60_T(bars, sym):
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
        sld = 2.0 * a; tpd = 1.0 * sld   # tight: 1.0×SL
        sl = entry - sld if sig == "BUY" else entry + sld
        tp = entry + tpd if sig == "BUY" else entry - tpd
        lot = size_for_risk(sld, upp)
        if lot < MIN_LOT: continue
        _, pnl, o, mc = sim_trade(bars, i+1, sig, entry, sl, tp, lot, upp, sp, 60)
        out.append((int(bars[i]['time']), sig, pnl, o, mc))
        last = i
    return out


# Consensus: multi-strategy confirmation, blended SL/TP
def strat_consensus(bars, sym):
    info = SYM[sym]; sp = info['spread']; upp = info['usd_per_pp']
    out = []; last = -100
    for i in range(210, len(bars) - 1):
        if i - last < 5: continue
        sigs = {}
        # rsi2
        ma200 = sma(bars, i, 200); r2 = rsi_at(bars, i, 2)
        if ma200 is not None and r2 is not None:
            c = bars[i]['close']
            if c > ma200 and r2 < 10:  sigs["rsi2"] = "BUY"
            elif c < ma200 and r2 > 90: sigs["rsi2"] = "SELL"
        # 3day_reverse
        if i >= 2:
            b1, b2, b3 = bars[i-2], bars[i-1], bars[i]
            if b1['close']>b1['open'] and b2['close']>b2['open'] and b3['close']>b3['open']:
                sigs["3day"] = "SELL"
            elif b1['close']<b1['open'] and b2['close']<b2['open'] and b3['close']<b3['open']:
                sigs["3day"] = "BUY"
        # donchian20
        recent = bars[i-20:i]
        hh = max(r['high'] for r in recent); ll = min(r['low'] for r in recent)
        cur = bars[i]
        if cur['high'] >= hh: sigs["donch"] = "BUY"
        elif cur['low'] <= ll: sigs["donch"] = "SELL"
        # momentum60
        a = atr(bars, i, 14)
        if a is not None and a > 0:
            ret60 = bars[i]['close'] - bars[i-60]['close']
            if abs(ret60) >= 2 * a:
                sigs["mom"] = "BUY" if ret60 > 0 else "SELL"
        # bb_extreme
        win = [bars[k]['close'] for k in range(i-19, i+1)]
        mid = sum(win) / 20
        var = sum((c-mid)**2 for c in win) / 20
        std = var ** 0.5
        if std > 0:
            upper = mid + 2.5 * std; lower = mid - 2.5 * std
            c = bars[i]['close']
            if c < lower: sigs["bb"] = "BUY"
            elif c > upper: sigs["bb"] = "SELL"

        if not sigs: continue
        buys = [k for k, v in sigs.items() if v == "BUY"]
        sells = [k for k, v in sigs.items() if v == "SELL"]
        HIGH_WR = {"rsi2", "3day"}
        group = buys if len(buys) > len(sells) else (sells if sells else [])
        if len(group) < 2: continue
        if not (any(k in HIGH_WR for k in group) and any(k not in HIGH_WR for k in group)):
            continue
        sig = "BUY" if group is buys else "SELL"
        if a is None or a == 0: continue
        if i + 1 >= len(bars): break
        entry = bars[i+1]['open'] + (sp if sig == "BUY" else 0)
        sld = 1.75 * a; tpd = 1.5 * a
        sl = entry - sld if sig == "BUY" else entry + sld
        tp = entry + tpd if sig == "BUY" else entry - tpd
        lot = size_for_risk(sld, upp)
        if lot < MIN_LOT: continue
        _, pnl, o, mc = sim_trade(bars, i+1, sig, entry, sl, tp, lot, upp, sp, 20)
        out.append((int(bars[i]['time']), sig, pnl, o, mc))
        last = i
    return out


STRATS = [
    ("donchian20_T", strat_donchian20_T),
    ("momentum60_T", strat_momentum60_T),
    ("consensus",    strat_consensus),
]

print("\nFetching bars and running variants...")
all_trades = defaultdict(list)
for sym in SYMBOLS:
    if sym not in SYM: continue
    bars = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 2000)
    if bars is None or len(bars) < 250: continue
    for name, fn in STRATS:
        try:
            for t in fn(bars, sym):
                all_trades[name].append((sym, *t))
        except Exception as e:
            print(f"  {sym} {name}: ERROR {e}")
    print(f"  {sym}: "
          + ", ".join(f"{name}={sum(1 for x in all_trades[name] if x[0]==sym)}" for name, _ in STRATS))


def analyze(name, trades):
    if not trades: return None
    n = len(trades)
    pnls = np.array([t[3] for t in trades])
    outcomes = [t[4] for t in trades]
    mc = np.array([t[5] for t in trades])
    touched = mc >= NEAR_SL_PCT
    n_touched = int(touched.sum())
    if n_touched > 0:
        n_recov = int((pnls[touched] > 0).sum())
        recovery_rate = n_recov / n_touched
    else:
        n_recov = 0; recovery_rate = 0.0
    R = abs(np.mean([t[3] for t in trades if t[4] == "SL"])) if any(t[4] == "SL" for t in trades) else RISK_PER_TRADE_USD
    untouched_pnl = float(pnls[~touched].sum())
    natural_total = float(pnls.sum())
    legacy_total = untouched_pnl + (-0.7 * R * n_touched)
    hard = mc >= HARD_CLOSE_PCT
    soft = touched & ~hard
    n_hard = int(hard.sum())
    rescue_total = untouched_pnl + float(pnls[soft].sum()) + (-0.9 * R * n_hard)
    return dict(name=name, n=n, n_touched=n_touched, n_recov=n_recov,
                recovery_rate=recovery_rate, natural_total=natural_total,
                legacy_total=legacy_total, rescue_total=rescue_total, avg_R=R)


results = [analyze(name, all_trades[name]) for name, _ in STRATS]
results = [r for r in results if r is not None]

print("\n" + "=" * 110)
print(f"VARIANT RESCUE ANALYSIS  —  risk ${RISK_PER_TRADE_USD}/trade  —  "
      f"NEAR_SL={NEAR_SL_PCT*100:.0f}%, HARD_CLOSE={HARD_CLOSE_PCT*100:.0f}%")
print("=" * 110)
hdr = (f"{'Strategy':<14} {'N':>5} {'Touched':>8} {'Recov%':>7} "
       f"{'NATURAL$':>12} {'LEGACY$':>12} {'RESCUE$':>12}  {'R-vs-L':>10}")
print(hdr)
print("-" * len(hdr))
totals = {"natural": 0.0, "legacy": 0.0, "rescue": 0.0, "n": 0, "touched": 0, "recov": 0}
for r in results:
    diff = r['rescue_total'] - r['legacy_total']
    print(f"{r['name']:<14} {r['n']:>5} {r['n_touched']:>8} {r['recovery_rate']*100:>6.1f}% "
          f"{r['natural_total']:>+12.2f} {r['legacy_total']:>+12.2f} "
          f"{r['rescue_total']:>+12.2f}  {diff:>+10.2f}")
    totals["natural"] += r['natural_total']
    totals["legacy"]  += r['legacy_total']
    totals["rescue"]  += r['rescue_total']
    totals["n"]       += r['n']
    totals["touched"] += r['n_touched']
    totals["recov"]   += r['n_recov']
print("-" * len(hdr))
agg = (100.0 * totals["recov"] / totals["touched"]) if totals["touched"] else 0.0
print(f"{'TOTAL':<14} {totals['n']:>5} {totals['touched']:>8} {agg:>6.1f}% "
      f"{totals['natural']:>+12.2f} {totals['legacy']:>+12.2f} "
      f"{totals['rescue']:>+12.2f}  {totals['rescue']-totals['legacy']:>+10.2f}")

print("\nPer-strategy recovery rate (for loser_rescue.py _MEASURED_RECOVERY update):")
for r in results:
    print(f"    \"{r['name']}\": {r['recovery_rate']:.3f},")

mt5.shutdown()
