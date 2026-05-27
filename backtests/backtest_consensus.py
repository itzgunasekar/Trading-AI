"""
Backtest v14 — test multi-strategy confirmation filter.

Hypothesis: when 2+ different strategies on the same symbol agree on direction,
the trade has higher win probability than either alone.

Tests:
  BASE  : current setup (every strategy trades independently)
  CONF2 : only trade if 2 or more strategies fire same direction same bar
  CONF3 : require 3+ strategies to agree
  CONF_HIGHWR : require at least 1 high-WR strategy (rsi2 or 3day_reverse) + 1 other

For each variant: trade count, win rate, $/day, max DD.
"""

from datetime import datetime, timezone
from collections import defaultdict
import MetaTrader5 as mt5
import numpy as np

SYMBOLS = ["XAUUSD","XAGUSD","US500","EURUSD","GBPUSD","USDJPY","GBPJPY",
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
def sma(bars, idx, period):
    if idx < period: return None
    return sum(bars[k]['close'] for k in range(idx-period+1, idx+1))/period
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
def sim(bars, ei, dirn, entry, sl, tp, lot, upp, sp, mb):
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

# ===========================================================================
# Detect each strategy's signal (or None) at every D1 bar i
# Returns dict: {strategy_name: ("BUY"/"SELL"/None, sl_atr_mult, tp_atr_mult, params)}
# ===========================================================================
def signal_donchian20(bars, i):
    if i < 25: return None
    r = bars[i-20:i]
    hh = max(b['high'] for b in r); ll = min(b['low'] for b in r)
    cur = bars[i]
    if cur['high'] >= hh: return ("BUY", 2.0, 3.0)
    if cur['low'] <= ll: return ("SELL", 2.0, 3.0)
    return None

def signal_momentum60(bars, i):
    if i < 65: return None
    a = atr(bars, i)
    if a is None: return None
    r60 = bars[i]['close'] - bars[i-60]['close']
    if abs(r60) < 2*a: return None
    return ("BUY" if r60 > 0 else "SELL", 2.0, 1.5)

def signal_rsi2(bars, i):
    if i < 210: return None
    ma200 = sma(bars, i, 200); r = rsi_at(bars, i, 2)
    if ma200 is None or r is None: return None
    c = bars[i]['close']
    if c > ma200 and r < 10: return ("BUY", 2.0, 0.75)
    if c < ma200 and r > 90: return ("SELL", 2.0, 0.75)
    return None

def signal_3day_reverse(bars, i):
    if i < 5: return None
    b1,b2,b3 = bars[i-2],bars[i-1],bars[i]
    au = b1['close']>b1['open'] and b2['close']>b2['open'] and b3['close']>b3['open']
    ad = b1['close']<b1['open'] and b2['close']<b2['open'] and b3['close']<b3['open']
    if au: return ("SELL", 1.5, 0.67)
    if ad: return ("BUY", 1.5, 0.67)
    return None

def signal_bb_extreme(bars, i):
    if i < 25: return None
    win = [bars[k]['close'] for k in range(i-19, i+1)]
    mid = sum(win)/20
    std = (sum((c-mid)**2 for c in win)/20)**0.5
    if std == 0: return None
    upper = mid + 2.5*std; lower = mid - 2.5*std
    c = bars[i]['close']
    if c < lower: return ("BUY", 1.5, ("MID", mid))
    if c > upper: return ("SELL", 1.5, ("MID", mid))
    return None

STRATS = {
    "donchian20": signal_donchian20,
    "momentum60": signal_momentum60,
    "rsi2":       signal_rsi2,
    "3day_reverse": signal_3day_reverse,
    "bb_extreme": signal_bb_extreme,
}
HIGH_WR = {"rsi2", "3day_reverse"}

# ===========================================================================
# Variant runner
# Mode "base": every strategy that fires opens its own trade (current bot behavior)
# Mode "conf2": only one trade per bar if 2+ strategies agree on direction
# Mode "conf3": only one trade per bar if 3+ strategies agree
# Mode "conf_highwr": one trade if (≥1 high-WR strat) + (≥1 other) agree
# When mode triggers, use the AVERAGE of the agreeing strategies' SL/TP atr_mults
# ===========================================================================
def run_variant(mode, bars, sym):
    info = SYM[sym]; sp = info['spread']; upp = info['usd_per_pp']
    trades = []
    last_entry_idx = -100
    for i in range(220, len(bars)-1):
        if i - last_entry_idx < 5: continue   # rate limit
        sigs = {}
        for name, fn in STRATS.items():
            s = fn(bars, i)
            if s is not None:
                sigs[name] = s
        if not sigs: continue

        # Decide which signals to act on per mode
        chosen = None
        if mode == "base":
            # Just take ONE arbitrary signal per bar (simulates 1-trade-per-bar rate cap)
            # Take whichever signal fires; this approximates portfolio behavior at bar level.
            # For ranking, prefer high-WR if available.
            key = next((k for k in sigs if k in HIGH_WR), next(iter(sigs)))
            chosen = [(key, sigs[key])]
        else:
            # Count agreement on direction
            buys  = [(k, v) for k, v in sigs.items() if v[0] == "BUY"]
            sells = [(k, v) for k, v in sigs.items() if v[0] == "SELL"]
            group = buys if len(buys) > len(sells) else sells
            n_needed = {"conf2": 2, "conf3": 3, "conf_highwr": 2}[mode]
            if len(group) < n_needed:
                continue
            if mode == "conf_highwr":
                has_high = any(k in HIGH_WR for k, _ in group)
                has_other = any(k not in HIGH_WR for k, _ in group)
                if not (has_high and has_other):
                    continue
            chosen = group

        # Build trade using average SL/TP atr multipliers
        dirn = chosen[0][1][0]
        sl_atrs = [v[1] for _, v in chosen]
        tp_atrs = []
        target_mid = None
        for _, v in chosen:
            tp_val = v[2]
            if isinstance(tp_val, tuple) and tp_val[0] == "MID":
                target_mid = tp_val[1]
            else:
                tp_atrs.append(tp_val)
        sl_atr_m = sum(sl_atrs) / len(sl_atrs)
        tp_atr_m = sum(tp_atrs) / len(tp_atrs) if tp_atrs else 1.5

        a = atr(bars, i, 14)
        if a is None or a == 0: continue
        if i+1 >= len(bars): break
        entry = bars[i+1]['open'] + (sp if dirn=="BUY" else 0)
        sl_d = sl_atr_m * a
        tp_d = tp_atr_m * a
        if target_mid is not None:
            tp = target_mid
        else:
            tp = entry + tp_d if dirn=="BUY" else entry - tp_d
        if dirn=="BUY": sl=entry-sl_d
        else:           sl=entry+sl_d
        if abs(tp - entry) < 0.3 * a: continue
        lot = size_for_risk(sl_d, upp)
        if lot < MIN_LOT: continue
        _, pnl, out = sim(bars, i+1, dirn, entry, sl, tp, lot, upp, sp, 40)
        trades.append((int(bars[i+1]['time']), dirn, pnl, out, len(chosen)))
        last_entry_idx = i
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
    return dict(n=len(trades), total=float(pnls.sum()), oos=float(oos),
                wr=100*len(wins)/len(pnls), dd=float(dd),
                per_day=float(pnls.sum())/max(days,1))

print("\n" + "="*110)
print(f"MULTI-STRATEGY CONFIRMATION TEST — D1 — risk ${RISK_PER_TRADE_USD}/trade")
print("="*110)
print(f"{'Variant':<13} {'N trades':>10} {'Win%':>7} {'Total$':>12} {'OOS$':>10} {'MaxDD':>11} {'$/day':>9}")

for mode in ["base", "conf2", "conf3", "conf_highwr"]:
    all_trades_combined = []
    n_winners = 0
    per_sym_results = []
    for sym in SYMBOLS:
        if sym not in SYM: continue
        bars = fetch(sym, mt5.TIMEFRAME_D1, 2000)
        if bars is None or len(bars) < 300: continue
        days = (bars[-1]['time'] - bars[0]['time'])/86400
        try:
            tr = run_variant(mode, bars, sym)
            r = stats(tr, days)
            if r and r['total'] > 0 and r['oos'] > 0 and r['n'] >= 10:
                per_sym_results.append({**r, "sym": sym})
                n_winners += 1
            all_trades_combined.extend(tr)
        except Exception as e:
            pass
    if not all_trades_combined:
        print(f"{mode:<13} no trades")
        continue
    pnls_all = np.array([t[2] for t in all_trades_combined])
    wins_all = pnls_all[pnls_all > 0]
    days_total = sum((fetch(s, mt5.TIMEFRAME_D1, 2000)[-1]['time'] -
                       fetch(s, mt5.TIMEFRAME_D1, 2000)[0]['time'])/86400
                      for s in SYMBOLS[:1]) or 1
    total_pd = sum(r['per_day'] for r in per_sym_results)
    total_oos = sum(r['oos'] for r in per_sym_results)
    total_total = sum(r['total'] for r in per_sym_results)
    total_dd = sum(r['dd'] for r in per_sym_results)
    print(f"{mode:<13} {len(all_trades_combined):>10} {100*len(wins_all)/len(pnls_all):>6.1f}% "
          f"{total_total:>+12.2f} {total_oos:>+10.2f} {total_dd:>+11.2f} {total_pd:>+9.2f}")

mt5.shutdown()
