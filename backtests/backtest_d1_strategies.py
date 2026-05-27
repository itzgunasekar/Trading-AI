"""
Backtest v9 — Strategies actually used by profitable institutions.

CATEGORIES TESTED:
  A. D1 Trend Following (managed futures / CTA approach — proven edge)
       A1. D1 Donchian 20-bar breakout
       A2. D1 simple momentum (60-day return sign)
       A3. D1 dual MA crossover (50/200)
  B. Mean Reversion at extremes
       B1. 3-consecutive-day reverse (D1)
       B2. RSI(2) on D1 (Connors classic)
       B3. Bollinger %b extreme on D1
  C. Time-of-day pattern (London close fade)
  D. Multi-asset portfolio (diversify across uncorrelated FX pairs)

All across 16 instruments, all with REALISTIC next-bar entry.
"""

from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict
import MetaTrader5 as mt5
import numpy as np

SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "AUDUSD", "USDCAD",
    "USDCHF", "EURJPY", "AUDJPY", "NZDUSD", "EURGBP",
    "XAUUSD", "XAGUSD", "US30", "US500", "UK100",
]
RISK_PER_TRADE_USD = 50.0
MIN_LOT = 0.01; MAX_LOT = 100.0; LOT_STEP = 0.01

print(">>> connecting...")
mt5.initialize()

SYM = {}
for s in SYMBOLS:
    mt5.symbol_select(s, True)
    info = mt5.symbol_info(s)
    if info is None: continue
    pip = 10 * info.trade_tick_size
    usd_per_pp = (info.trade_tick_value / info.trade_tick_size
                  if info.trade_tick_size > 0 else info.trade_contract_size)
    if s in ("XAUUSD",): spread = 0.30
    elif s in ("XAGUSD",): spread = 0.03
    elif s in ("US30","US500","UK100"): spread = 0.50
    else: spread = 1.5 * pip
    SYM[s] = dict(pip=pip, spread=spread, usd_per_pp=usd_per_pp, digits=info.digits)

def fetch(s, tf, n):
    return mt5.copy_rates_from_pos(s, tf, 0, n)

def normalize_lot(v):
    v = max(MIN_LOT, min(MAX_LOT, v))
    return round(round(v/LOT_STEP)*LOT_STEP, 2)

def size_for_risk(d, usd_per_pp):
    if d <= 0 or usd_per_pp <= 0: return 0
    return normalize_lot(RISK_PER_TRADE_USD/(d*usd_per_pp))

def sim_trade(bars, entry_idx, direction, entry, sl, tp, lot, usd_per_pp, spread, max_bars):
    j = entry_idx + 1
    end = min(len(bars), entry_idx + max_bars + 1)
    while j < end:
        b = bars[j]
        if direction == "BUY":
            if b['low']  <= sl: return j, (sl - entry)*lot*usd_per_pp, "SL"
            if b['high'] >= tp: return j, (tp - entry)*lot*usd_per_pp, "TP"
        else:
            if b['high'] + spread >= sl: return j, (entry - sl)*lot*usd_per_pp, "SL"
            if b['low']  + spread <= tp: return j, (entry - tp)*lot*usd_per_pp, "TP"
        j += 1
    exit_px = bars[end-1]['close'] + (0 if direction == "BUY" else spread)
    pnl = (exit_px - entry)*lot*usd_per_pp if direction == "BUY" else (entry - exit_px)*lot*usd_per_pp
    return end-1, pnl, "TIMEOUT"

def atr(bars, idx, period=14):
    if idx < period+1: return None
    trs = []
    for k in range(idx-period, idx):
        h=bars[k]['high']; l=bars[k]['low']; pc=bars[k-1]['close']
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs)/len(trs)

def rsi_at(bars, idx, period):
    if idx < period+1: return None
    closes = [bars[k]['close'] for k in range(idx-period, idx+1)]
    g=l=0.0
    for i in range(1, len(closes)):
        d = closes[i]-closes[i-1]
        if d >= 0: g += d
        else: l -= d
    ag, al = g/period, l/period
    if al == 0: return 100.0
    rs = ag/al
    return 100 - 100/(1+rs)

def sma(bars, idx, period, field='close'):
    if idx < period: return None
    return sum(bars[k][field] for k in range(idx-period+1, idx+1)) / period

# ===========================================================================
# A1. D1 Donchian 20-bar breakout (classic Turtle)
# ===========================================================================
def strat_donchian20_d1(bars, sym):
    info = SYM[sym]; spread = info['spread']; usd_per_pp = info['usd_per_pp']
    trades = []; last_entry_idx = -100
    for i in range(25, len(bars)-1):
        if i - last_entry_idx < 5: continue
        recent = bars[i-20:i]
        hh = max(r['high'] for r in recent); ll = min(r['low'] for r in recent)
        a = atr(bars, i, 14)
        if a is None or a == 0: continue
        cur = bars[i]
        sig = None
        if cur['high'] >= hh: sig = "BUY"
        elif cur['low'] <= ll: sig = "SELL"
        if sig is None: continue
        # Enter next bar open
        if i+1 >= len(bars): break
        nb = bars[i+1]
        entry = nb['open'] + (spread if sig == "BUY" else 0)
        sl_dist = 2.0 * a
        if sig == "BUY": sl = entry - sl_dist; tp = entry + 3.0*sl_dist
        else:            sl = entry + sl_dist; tp = entry - 3.0*sl_dist
        lot = size_for_risk(sl_dist, usd_per_pp)
        if lot < MIN_LOT: continue
        _, pnl, o = sim_trade(bars, i+1, sig, entry, sl, tp, lot, usd_per_pp, spread, max_bars=40)
        trades.append((int(cur['time']), sig, pnl, o))
        last_entry_idx = i
    return trades

# ===========================================================================
# A2. D1 60-day momentum
# ===========================================================================
def strat_mom60_d1(bars, sym):
    info = SYM[sym]; spread = info['spread']; usd_per_pp = info['usd_per_pp']
    trades = []; last_entry_idx = -100
    for i in range(65, len(bars)-1):
        if i - last_entry_idx < 10: continue
        ret60 = bars[i]['close'] - bars[i-60]['close']
        if abs(ret60) < 2 * atr(bars, i, 14): continue   # need decent move
        sig = "BUY" if ret60 > 0 else "SELL"
        a = atr(bars, i, 14)
        if a is None: continue
        if i+1 >= len(bars): break
        nb = bars[i+1]
        entry = nb['open'] + (spread if sig == "BUY" else 0)
        sl_dist = 2.0 * a; tp_dist = 3.0 * a
        if sig == "BUY": sl = entry - sl_dist; tp = entry + tp_dist
        else:            sl = entry + sl_dist; tp = entry - tp_dist
        lot = size_for_risk(sl_dist, usd_per_pp)
        if lot < MIN_LOT: continue
        _, pnl, o = sim_trade(bars, i+1, sig, entry, sl, tp, lot, usd_per_pp, spread, max_bars=60)
        trades.append((int(bars[i]['time']), sig, pnl, o))
        last_entry_idx = i
    return trades

# ===========================================================================
# A3. D1 50/200 MA crossover
# ===========================================================================
def strat_ma_cross_d1(bars, sym):
    info = SYM[sym]; spread = info['spread']; usd_per_pp = info['usd_per_pp']
    trades = []
    open_dir = None; open_idx = None; open_entry = None; open_sl = None; open_tp = None; open_lot = None
    for i in range(205, len(bars)-1):
        ma50_cur = sma(bars, i, 50)
        ma200_cur = sma(bars, i, 200)
        ma50_prev = sma(bars, i-1, 50)
        ma200_prev = sma(bars, i-1, 200)
        if any(x is None for x in (ma50_cur, ma200_cur, ma50_prev, ma200_prev)): continue
        a = atr(bars, i, 14)
        if a is None or a == 0: continue
        cross_up   = ma50_prev <= ma200_prev and ma50_cur > ma200_cur
        cross_down = ma50_prev >= ma200_prev and ma50_cur < ma200_cur
        # close any existing position on cross opposite
        if open_dir is not None:
            close_signal = (open_dir == "BUY" and cross_down) or (open_dir == "SELL" and cross_up)
            # also check SL/TP hit since open_idx
            j = open_idx + 1
            hit = None
            while j <= i:
                b = bars[j]
                if open_dir == "BUY":
                    if b['low']  <= open_sl: hit = ('SL', open_sl); break
                    if b['high'] >= open_tp: hit = ('TP', open_tp); break
                else:
                    if b['high'] + spread >= open_sl: hit = ('SL', open_sl); break
                    if b['low']  + spread <= open_tp: hit = ('TP', open_tp); break
                j += 1
            if hit:
                pnl = ((hit[1]-open_entry) if open_dir == "BUY" else (open_entry-hit[1])) * open_lot * usd_per_pp
                trades.append((int(bars[open_idx]['time']), open_dir, pnl, hit[0]))
                open_dir = None
            elif close_signal:
                exit_px = bars[i]['close'] + (0 if open_dir == "BUY" else spread)
                pnl = ((exit_px-open_entry) if open_dir == "BUY" else (open_entry-exit_px)) * open_lot * usd_per_pp
                trades.append((int(bars[open_idx]['time']), open_dir, pnl, "CROSS"))
                open_dir = None
        # new entry
        if open_dir is None and (cross_up or cross_down):
            sig = "BUY" if cross_up else "SELL"
            if i+1 >= len(bars): break
            entry = bars[i+1]['open'] + (spread if sig == "BUY" else 0)
            sl_dist = 3.0 * a
            if sig == "BUY": sl = entry - sl_dist; tp = entry + 6.0*sl_dist
            else:            sl = entry + sl_dist; tp = entry - 6.0*sl_dist
            lot = size_for_risk(sl_dist, usd_per_pp)
            if lot < MIN_LOT: continue
            open_dir, open_idx, open_entry, open_sl, open_tp, open_lot = sig, i+1, entry, sl, tp, lot
    return trades

# ===========================================================================
# B1. 3-consecutive-day reverse (mean reversion D1)
# ===========================================================================
def strat_3day_reverse_d1(bars, sym):
    info = SYM[sym]; spread = info['spread']; usd_per_pp = info['usd_per_pp']
    trades = []
    for i in range(5, len(bars)-1):
        b1 = bars[i-2]; b2 = bars[i-1]; b3 = bars[i]
        all_down = (b1['close'] < b1['open'] and b2['close'] < b2['open'] and b3['close'] < b3['open'])
        all_up   = (b1['close'] > b1['open'] and b2['close'] > b2['open'] and b3['close'] > b3['open'])
        if not (all_up or all_down): continue
        sig = "SELL" if all_up else "BUY"
        a = atr(bars, i, 14)
        if a is None: continue
        if i+1 >= len(bars): break
        entry = bars[i+1]['open'] + (spread if sig == "BUY" else 0)
        sl_dist = 1.5 * a; tp_dist = 1.0 * a
        if sig == "BUY": sl = entry - sl_dist; tp = entry + tp_dist
        else:            sl = entry + sl_dist; tp = entry - tp_dist
        lot = size_for_risk(sl_dist, usd_per_pp)
        if lot < MIN_LOT: continue
        _, pnl, o = sim_trade(bars, i+1, sig, entry, sl, tp, lot, usd_per_pp, spread, max_bars=10)
        trades.append((int(bars[i]['time']), sig, pnl, o))
    return trades

# ===========================================================================
# B2. RSI(2) D1 Connors
# ===========================================================================
def strat_rsi2_d1(bars, sym):
    info = SYM[sym]; spread = info['spread']; usd_per_pp = info['usd_per_pp']
    trades = []; last_entry_idx = -100
    for i in range(210, len(bars)-1):
        if i - last_entry_idx < 3: continue
        ma200 = sma(bars, i, 200)
        if ma200 is None: continue
        rsi = rsi_at(bars, i, 2)
        if rsi is None: continue
        sig = None
        if bars[i]['close'] > ma200 and rsi < 10: sig = "BUY"
        elif bars[i]['close'] < ma200 and rsi > 90: sig = "SELL"
        if sig is None: continue
        a = atr(bars, i, 14)
        if a is None: continue
        if i+1 >= len(bars): break
        entry = bars[i+1]['open'] + (spread if sig == "BUY" else 0)
        sl_dist = 2.0 * a; tp_dist = 1.5 * a
        if sig == "BUY": sl = entry - sl_dist; tp = entry + tp_dist
        else:            sl = entry + sl_dist; tp = entry - tp_dist
        lot = size_for_risk(sl_dist, usd_per_pp)
        if lot < MIN_LOT: continue
        _, pnl, o = sim_trade(bars, i+1, sig, entry, sl, tp, lot, usd_per_pp, spread, max_bars=15)
        trades.append((int(bars[i]['time']), sig, pnl, o))
        last_entry_idx = i
    return trades

# ===========================================================================
# B3. D1 Bollinger %b extreme reversal
# ===========================================================================
def strat_bb_extreme_d1(bars, sym):
    info = SYM[sym]; spread = info['spread']; usd_per_pp = info['usd_per_pp']
    trades = []; last_entry_idx = -100
    for i in range(25, len(bars)-1):
        if i - last_entry_idx < 3: continue
        win = [bars[k]['close'] for k in range(i-19, i+1)]
        mid = sum(win)/20
        var = sum((c-mid)**2 for c in win)/20
        std = var**0.5
        if std == 0: continue
        upper = mid + 2.5*std; lower = mid - 2.5*std
        c = bars[i]['close']
        sig = None
        if c < lower: sig = "BUY"
        elif c > upper: sig = "SELL"
        if sig is None: continue
        a = atr(bars, i, 14)
        if a is None: continue
        if i+1 >= len(bars): break
        entry = bars[i+1]['open'] + (spread if sig == "BUY" else 0)
        sl_dist = 1.5 * a
        if sig == "BUY": sl = entry - sl_dist; tp = mid
        else:            sl = entry + sl_dist; tp = mid
        if abs(tp - entry) < 0.5 * a: continue
        lot = size_for_risk(sl_dist, usd_per_pp)
        if lot < MIN_LOT: continue
        _, pnl, o = sim_trade(bars, i+1, sig, entry, sl, tp, lot, usd_per_pp, spread, max_bars=15)
        trades.append((int(bars[i]['time']), sig, pnl, o))
        last_entry_idx = i
    return trades

# ===========================================================================
def report(name, trades, days):
    if not trades:
        return dict(name=name, n=0, total=0, oos=0, wr=0, dd=0)
    pnls = np.array([t[2] for t in trades])
    wins = pnls[pnls > 0]
    eq=0; peak=0; dd=0
    for p in pnls:
        eq+=p; peak=max(peak,eq); dd=min(dd, eq-peak)
    split = trades[int(len(trades)*0.7)][0]
    oos = sum(t[2] for t in trades if t[0] >= split)
    sharpe = (pnls.mean()/pnls.std() * np.sqrt(252)) if pnls.std() > 0 else 0
    return dict(name=name, n=len(trades), total=float(pnls.sum()), oos=float(oos),
                wr=100*len(wins)/len(pnls), dd=float(dd), sharpe=sharpe,
                per_day=float(pnls.sum())/max(days,1))

print("\nRunning D1 strategies on all 16 symbols...")
all_results = []
for sym in SYMBOLS:
    if sym not in SYM: continue
    bars_d1 = fetch(sym, mt5.TIMEFRAME_D1, 2000)
    if bars_d1 is None or len(bars_d1) < 250: continue
    days = (bars_d1[-1]['time'] - bars_d1[0]['time']) / 86400
    for name, fn in [
        ("A1 Donchian20_D1",  strat_donchian20_d1),
        ("A2 Momentum60_D1",  strat_mom60_d1),
        ("A3 MAcross50/200",  strat_ma_cross_d1),
        ("B1 3day_reverse",   strat_3day_reverse_d1),
        ("B2 RSI2_D1",        strat_rsi2_d1),
        ("B3 BB_extreme_D1",  strat_bb_extreme_d1),
    ]:
        try:
            trades = fn(bars_d1, sym)
            r = report(name, trades, days)
            r['sym'] = sym
            all_results.append(r)
        except Exception as e:
            print(f"  {sym} {name}: ERROR {e}")
    print(f"  {sym}: D1 history={int(days)}d")

print("\n" + "="*110)
print(f"D1 STRATEGY RESULTS — risk ${RISK_PER_TRADE_USD}/trade")
print("="*110)
print(f"{'Symbol':<8} {'Strategy':<22} {'N':>5} {'Win%':>5} {'Total$':>10} {'OOS$':>9} {'MaxDD':>9} {'$/day':>7} {'Sharpe':>7}")
all_results.sort(key=lambda r: -(r['total'] + r['oos']))
for r in all_results:
    if r['n'] == 0: continue
    print(f"{r['sym']:<8} {r['name']:<22} {r['n']:>5} {r['wr']:>4.1f}% {r['total']:>+10.2f} "
          f"{r['oos']:>+9.2f} {r['dd']:>+9.2f} {r['per_day']:>+7.3f} {r['sharpe']:>+7.2f}")

# Filter — STRICT: both total AND OOS positive AND n >= 20
print("\n" + "="*110)
print("SURVIVING STRATEGIES (total>0 AND oos>0 AND n>=20)")
print("="*110)
winners = [r for r in all_results if r['total'] > 0 and r['oos'] > 0 and r['n'] >= 20]
winners.sort(key=lambda r: -r['oos'])
if not winners:
    print("  NONE.")
else:
    for r in winners:
        print(f"  {r['sym']:<8} {r['name']:<22} n={r['n']:>4}  total=${r['total']:+9.2f}  OOS=${r['oos']:+8.2f}  "
              f"$/day=${r['per_day']:+6.3f}  sharpe={r['sharpe']:+5.2f}")

if winners:
    # Diversified portfolio: sum per-day P&L of all winners (one trade per setup, low correlation)
    total_per_day = sum(r['per_day'] for r in winners)
    total_oos = sum(r['oos'] for r in winners)
    print(f"\n  PORTFOLIO IF ALL WINNERS COMBINED:")
    print(f"    backtest $/day  : ${total_per_day:+.3f}")
    print(f"    OOS total       : ${total_oos:+.2f}")
    print(f"    Risk per trade  : ${RISK_PER_TRADE_USD}")
    if total_per_day > 0:
        scale = 100/total_per_day
        print(f"    Scale to $100/day: risk per trade = ${RISK_PER_TRADE_USD*scale:.0f}")

mt5.shutdown()
