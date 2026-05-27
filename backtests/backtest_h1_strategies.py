"""
Backtest v12 — fast comprehensive test of user's three asks:
  1. Multi-timeframe: H1, M30, M15 of the WINNING strategies
  2. Trailing stop variants on D1 (BE-move + trail vs fixed)
  3. Daily bucket TP simulation

Uses bar-based simulation (no M1 cache) so it runs fast.
"""

from datetime import datetime, timezone
from collections import defaultdict, Counter
import MetaTrader5 as mt5
import numpy as np

SYMBOLS = ["XAUUSD","XAGUSD","US500","US30","EURUSD","GBPUSD","USDJPY","GBPJPY",
           "AUDJPY","EURJPY","AUDUSD","NZDUSD","EURGBP","CADJPY","CHFJPY","NZDJPY",
           "EURCHF","EURAUD","EURCAD","GBPAUD","AUDCAD"]
RISK_PER_TRADE_USD = 300.0
MIN_LOT = 0.01; MAX_LOT = 100.0; LOT_STEP = 0.01

print(">>> connecting...")
mt5.initialize()

SYM = {}
for s in SYMBOLS:
    mt5.symbol_select(s, True)
    info = mt5.symbol_info(s)
    if info is None: continue
    pip = 10 * info.trade_tick_size
    upp = (info.trade_tick_value / info.trade_tick_size
           if info.trade_tick_size > 0 else info.trade_contract_size)
    if s in ("XAUUSD",): sp = 0.30
    elif s in ("XAGUSD",): sp = 0.03
    elif s in ("US30","US500"): sp = 0.50
    else: sp = 1.5 * pip
    SYM[s] = dict(pip=pip, spread=sp, usd_per_pp=upp, digits=info.digits)

def fetch(s, tf, n): return mt5.copy_rates_from_pos(s, tf, 0, n)
def normalize_lot(v):
    v = max(MIN_LOT, min(MAX_LOT, v)); return round(round(v/LOT_STEP)*LOT_STEP, 2)
def size_for_risk(d, upp):
    if d <= 0 or upp <= 0: return 0
    return normalize_lot(RISK_PER_TRADE_USD/(d*upp))
def atr(bars, idx, period=14):
    if idx < period+1: return None
    return sum(max(bars[k]['high']-bars[k]['low'],
                   abs(bars[k]['high']-bars[k-1]['close']),
                   abs(bars[k]['low']-bars[k-1]['close']))
               for k in range(idx-period, idx))/period
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
    return 100 - 100/(1+ag/al)
def sma(bars, idx, period):
    if idx < period: return None
    return sum(bars[k]['close'] for k in range(idx-period+1, idx+1))/period

def sim_fixed(bars, ei, dirn, entry, sl, tp, lot, upp, sp, mb):
    """Standard SL/TP simulation."""
    j = ei + 1; end = min(len(bars), ei + mb + 1)
    while j < end:
        b = bars[j]
        if dirn == "BUY":
            if b['low']  <= sl: return j, (sl-entry)*lot*upp, "SL"
            if b['high'] >= tp: return j, (tp-entry)*lot*upp, "TP"
        else:
            if b['high']+sp >= sl: return j, (entry-sl)*lot*upp, "SL"
            if b['low'] +sp <= tp: return j, (entry-tp)*lot*upp, "TP"
        j += 1
    exit_px = bars[end-1]['close'] + (0 if dirn=="BUY" else sp)
    pnl = (exit_px-entry)*lot*upp if dirn=="BUY" else (entry-exit_px)*lot*upp
    return end-1, pnl, "TIMEOUT"

def sim_trailing(bars, ei, dirn, entry, sl, tp, lot, upp, sp, mb, atr_val,
                 be_at_atr=1.0, trail_at_atr=2.0, trail_dist_atr=1.0):
    """Trailing stop: move SL to BE once price moves be_at_atr×ATR in favor,
    then trail behind market by trail_dist_atr×ATR once price moves trail_at_atr×ATR.
    Original TP unchanged (or you can disable TP and let trail close everything)."""
    j = ei + 1; end = min(len(bars), ei + mb + 1)
    moved_to_be = False
    while j < end:
        b = bars[j]
        if dirn == "BUY":
            # Update SL based on highest high seen
            new_sl_be = entry if (b['high'] - entry) >= be_at_atr*atr_val else sl
            if not moved_to_be and new_sl_be > sl:
                sl = new_sl_be; moved_to_be = True
            if moved_to_be and (b['high'] - entry) >= trail_at_atr*atr_val:
                candidate = b['high'] - trail_dist_atr*atr_val
                if candidate > sl: sl = candidate
            if b['low']  <= sl: return j, (sl-entry)*lot*upp, "SL/TRAIL"
            if b['high'] >= tp: return j, (tp-entry)*lot*upp, "TP"
        else:
            new_sl_be = entry if (entry - b['low']) >= be_at_atr*atr_val else sl
            if not moved_to_be and new_sl_be < sl:
                sl = new_sl_be; moved_to_be = True
            if moved_to_be and (entry - b['low']) >= trail_at_atr*atr_val:
                candidate = b['low'] + trail_dist_atr*atr_val + sp
                if candidate < sl: sl = candidate
            if b['high']+sp >= sl: return j, (entry-sl)*lot*upp, "SL/TRAIL"
            if b['low'] +sp <= tp: return j, (entry-tp)*lot*upp, "TP"
        j += 1
    exit_px = bars[end-1]['close'] + (0 if dirn=="BUY" else sp)
    pnl = (exit_px-entry)*lot*upp if dirn=="BUY" else (entry-exit_px)*lot*upp
    return end-1, pnl, "TIMEOUT"

# ===========================================================================
# Strategy detectors — return signals; runner picks SL/TP and simulator
# ===========================================================================
def get_signals(strat, bars, sym):
    """Return list of (entry_idx, direction, sl_dist_atr, tp_dist_atr)."""
    info = SYM[sym]
    sigs = []
    if strat == "donchian20":
        last = -100
        for i in range(25, len(bars)-1):
            if i-last < 5: continue
            recent = bars[i-20:i]
            hh = max(r['high'] for r in recent); ll = min(r['low'] for r in recent)
            cur = bars[i]
            d = "BUY" if cur['high'] >= hh else ("SELL" if cur['low'] <= ll else None)
            if d:
                sigs.append((i, d, 2.0, 6.0))   # sl=2ATR tp=6ATR (3:1)
                last = i
    elif strat == "momentum60":
        last = -100
        for i in range(65, len(bars)-1):
            if i-last < 10: continue
            a = atr(bars, i)
            if a is None: continue
            r60 = bars[i]['close'] - bars[i-60]['close']
            if abs(r60) < 2*a: continue
            d = "BUY" if r60 > 0 else "SELL"
            sigs.append((i, d, 2.0, 3.0))   # sl=2 tp=3 (1.5:1)
            last = i
    elif strat == "rsi2":
        last = -100
        for i in range(210, len(bars)-1):
            if i-last < 3: continue
            ma200 = sma(bars, i, 200)
            r = rsi_at(bars, i, 2)
            if ma200 is None or r is None: continue
            c = bars[i]['close']
            d = "BUY" if (c > ma200 and r < 10) else ("SELL" if (c < ma200 and r > 90) else None)
            if d:
                sigs.append((i, d, 2.0, 1.5))   # sl=2 tp=1.5 (0.75:1)
                last = i
    elif strat == "bb_extreme":
        last = -100
        for i in range(25, len(bars)-1):
            if i-last < 3: continue
            win = [bars[k]['close'] for k in range(i-19, i+1)]
            mid = sum(win)/20
            std = (sum((c-mid)**2 for c in win)/20)**0.5
            if std == 0: continue
            upper = mid + 2.5*std; lower = mid - 2.5*std
            c = bars[i]['close']
            d = "BUY" if c < lower else ("SELL" if c > upper else None)
            if d:
                sigs.append((i, d, 1.5, 0.0, mid))   # tp = mid band
                last = i
    return sigs

def run_strategy(bars, sym, strat, max_hold, use_trail=False):
    info = SYM[sym]; sp = info['spread']; upp = info['usd_per_pp']
    trades = []
    for sig in get_signals(strat, bars, sym):
        if strat == "bb_extreme":
            i, dirn, sl_atr_m, _, mid = sig
        else:
            i, dirn, sl_atr_m, tp_atr_m = sig
        a = atr(bars, i)
        if a is None or a == 0: continue
        if i+1 >= len(bars): break
        entry = bars[i+1]['open'] + (sp if dirn=="BUY" else 0)
        sl_d = sl_atr_m * a
        if strat == "bb_extreme":
            tp = mid
        else:
            tp_d = tp_atr_m * a
            tp = entry + tp_d if dirn=="BUY" else entry - tp_d
        if dirn=="BUY": sl = entry - sl_d
        else:           sl = entry + sl_d
        if strat == "bb_extreme" and abs(tp-entry) < 0.5*a: continue
        lot = size_for_risk(sl_d, upp)
        if lot < MIN_LOT: continue
        if use_trail:
            _, pnl, out = sim_trailing(bars, i+1, dirn, entry, sl, tp, lot, upp, sp, max_hold, a)
        else:
            _, pnl, out = sim_fixed(bars, i+1, dirn, entry, sl, tp, lot, upp, sp, max_hold)
        trades.append((int(bars[i+1]['time']), dirn, pnl, out))
    return trades

def stats(trades, days):
    if not trades: return None
    pnls = np.array([t[2] for t in trades])
    wins = pnls[pnls > 0]
    eq=0; peak=0; dd=0
    for p in pnls:
        eq+=p; peak=max(peak,eq); dd=min(dd, eq-peak)
    split = trades[int(len(trades)*0.7)][0]
    oos = sum(t[2] for t in trades if t[0] >= split)
    sh = (pnls.mean()/pnls.std()*np.sqrt(252)) if pnls.std() > 0 else 0
    return dict(n=len(trades), total=float(pnls.sum()), oos=float(oos),
                wr=100*len(wins)/len(pnls), dd=float(dd), sharpe=sh,
                per_day=float(pnls.sum())/max(days,1))

# ===========================================================================
# TEST 1: same strategies on H1, M30, M15
# ===========================================================================
print("\n" + "="*100)
print("TEST 1 — winning D1 strategies on FASTER timeframes (H1, M30, M15)")
print("="*100)
print(f"{'Symbol':<8} {'TF':<4} {'Strat':<13} {'N':>5} {'Win%':>5} {'Total$':>10} {'OOS$':>9} {'$/day':>7}")
all_results = defaultdict(list)
strats_to_test = ["donchian20", "momentum60", "rsi2", "bb_extreme"]
tfs = [("H1", mt5.TIMEFRAME_H1, 5000, 48),
       ("M30", mt5.TIMEFRAME_M30, 8000, 96),
       ("M15", mt5.TIMEFRAME_M15, 12000, 192)]

for tf_name, tf, n_bars, max_hold in tfs:
    for sym in SYMBOLS:
        if sym not in SYM: continue
        bars = fetch(sym, tf, n_bars)
        if bars is None or len(bars) < 250: continue
        days = (bars[-1]['time'] - bars[0]['time'])/86400
        for strat in strats_to_test:
            try:
                trades = run_strategy(bars, sym, strat, max_hold, use_trail=False)
                r = stats(trades, days)
                if r and r['total'] > 0 and r['oos'] > 0 and r['n'] >= 20:
                    all_results[tf_name].append({**r, "sym":sym, "strat":strat})
                    print(f"{sym:<8} {tf_name:<4} {strat:<13} {r['n']:>5} {r['wr']:>4.1f}% "
                          f"{r['total']:>+10.2f} {r['oos']:>+9.2f} {r['per_day']:>+7.3f}")
            except Exception:
                pass

for tf_name in ("H1", "M30", "M15"):
    wins = all_results[tf_name]
    if wins:
        pd = sum(r['per_day'] for r in wins)
        print(f"\n  {tf_name} WINNERS: {len(wins)}  combined $/day=${pd:.2f}")
    else:
        print(f"\n  {tf_name} WINNERS: NONE — strategies don't survive at this timeframe")

# ===========================================================================
# TEST 2: D1 strategies WITH vs WITHOUT trailing stop
# ===========================================================================
print("\n" + "="*100)
print("TEST 2 — TRAILING STOP vs FIXED on D1 (BE-move at 1×ATR profit, trail at 2×ATR)")
print("="*100)
print(f"{'Symbol':<8} {'Strat':<13} {'Mode':<10} {'N':>5} {'Win%':>5} {'Total$':>10} {'OOS$':>9}")
trail_winners = []
fixed_winners = []
for sym in SYMBOLS:
    if sym not in SYM: continue
    bars = fetch(sym, mt5.TIMEFRAME_D1, 2000)
    if bars is None or len(bars) < 250: continue
    days = (bars[-1]['time'] - bars[0]['time'])/86400
    for strat in strats_to_test:
        try:
            t_fix = run_strategy(bars, sym, strat, 60, use_trail=False)
            t_trl = run_strategy(bars, sym, strat, 60, use_trail=True)
            r_fix = stats(t_fix, days); r_trl = stats(t_trl, days)
            if r_fix and r_fix['total'] > 0:
                fixed_winners.append({**r_fix, "sym":sym, "strat":strat})
            if r_trl and r_trl['total'] > 0:
                trail_winners.append({**r_trl, "sym":sym, "strat":strat})
            # show side-by-side
            if r_fix and r_trl:
                better = "TRAIL" if r_trl['total'] > r_fix['total'] else "FIXED"
                print(f"{sym:<8} {strat:<13} FIXED      {r_fix['n']:>5} {r_fix['wr']:>4.1f}% "
                      f"{r_fix['total']:>+10.2f} {r_fix['oos']:>+9.2f}")
                print(f"{sym:<8} {strat:<13} TRAIL      {r_trl['n']:>5} {r_trl['wr']:>4.1f}% "
                      f"{r_trl['total']:>+10.2f} {r_trl['oos']:>+9.2f}  ←{better} wins")
        except Exception:
            pass

print(f"\n  FIXED winners: {len(fixed_winners)}  total $/day=${sum(r['per_day'] for r in fixed_winners):.2f}")
print(f"  TRAIL winners: {len(trail_winners)}  total $/day=${sum(r['per_day'] for r in trail_winners):.2f}")
fixed_better = sum(1 for f in fixed_winners
                   if not any(t['sym']==f['sym'] and t['strat']==f['strat'] and t['total'] > f['total']
                              for t in trail_winners))
print(f"  Combos where FIXED beats TRAIL: {fixed_better} / {len(fixed_winners)}")

mt5.shutdown()
