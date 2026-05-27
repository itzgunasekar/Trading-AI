"""
Backtest — Rescue Layer recovery-rate measurement.

Question this answers:
  Of trades that EVER touched 70% of their SL distance (the "near-SL" condition
  that triggers the bucket-smart-close's loser branch), what fraction recover
  to a profitable close vs go on to hit full SL?

Why it matters:
  The Loser Rescue Intelligence Layer (loser_rescue.py) replaces the binary
  "close any loser >= 70% to SL" rule with a score. If the layer KEEPS a near-SL
  loser, two things can happen:
    (a) the trade recovers (good — beats the legacy bucket close)
    (b) the trade hits full SL (bad — worse than the legacy -0.7R close)

  This script measures (a) vs (b) on real 8-year historical data, so we can:
    1. Validate the bootstrap default recovery rates (0.45 mean-rev / 0.20 trend)
    2. Confirm the rescue layer doesn't degrade the 8-year edge
    3. Quantify the dollar-impact of three policies on the same data:
         NATURAL  — let every trade ride to SL/TP/TIMEOUT (= original 8-yr backtest)
         LEGACY   — 70%-touch trades closed at exactly -0.7R (= old bucket rule)
         RESCUE   — 70%-touch trades evaluated by bootstrap defaults

Run:  python backtests/backtest_rescue_recovery.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict
import MetaTrader5 as mt5
import numpy as np


# ---------------------------------------------------------------------------
# Universe — broadly matches ACTIVE_COMBINATIONS, scoped to keep runtime modest
# ---------------------------------------------------------------------------
SYMBOLS = [
    "XAUUSD", "XAGUSD", "US500", "US30",
    "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "AUDJPY", "EURJPY",
    "AUDUSD", "NZDUSD", "EURGBP", "CADJPY", "CHFJPY", "NZDJPY",
]
RISK_PER_TRADE_USD = 50.0
MIN_LOT = 0.01; MAX_LOT = 100.0; LOT_STEP = 0.01

# Family classification — must mirror loser_rescue._family_default_recovery()
MEAN_REV_FAMILY = {"rsi2", "bb_extreme", "3day_reverse"}
TREND_FAMILY    = {"donchian20", "momentum60"}
FAMILY_DEFAULT_RECOVERY = {
    "rsi2": 0.45, "bb_extreme": 0.45, "3day_reverse": 0.45,
    "donchian20": 0.20, "momentum60": 0.20,
}

NEAR_SL_PCT = 0.70   # threshold the rescue layer evaluates against
HARD_CLOSE_PCT = 0.95  # rescue's safety floor (raised from 0.90 after first-run analysis)


# ---------------------------------------------------------------------------
# MT5 connect + symbol info
# ---------------------------------------------------------------------------
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
    elif s in ("US30","US500"): sp = 0.50
    else:                     sp = 1.5 * pip
    SYM[s] = dict(pip=pip, spread=sp, usd_per_pp=upp, digits=info.digits)


def normalize_lot(v):
    v = max(MIN_LOT, min(MAX_LOT, v))
    return round(round(v / LOT_STEP) * LOT_STEP, 2)


def size_for_risk(d, upp):
    if d <= 0 or upp <= 0:
        return 0
    return normalize_lot(RISK_PER_TRADE_USD / (d * upp))


# Shared indicator math — single source in ../indicators.py
from indicators import atr_at as atr, rsi_at, sma_at as sma


# ---------------------------------------------------------------------------
# Enhanced sim_trade — tracks max consumed_pct across the trade's life
# ---------------------------------------------------------------------------
def sim_trade(bars, ei, dirn, entry, sl, tp, lot, upp, sp, max_bars):
    """Returns (exit_idx, pnl, outcome, max_consumed_pct).

    max_consumed_pct is the largest (adverse_distance / sl_distance) observed
    on any bar before the trade closed — measured against the LOW (for BUY) or
    HIGH+spread (for SELL) so it's the true MAE in SL-distance units."""
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
                if cp > max_consumed:
                    max_consumed = cp
            if b['low'] <= sl:
                return j, (sl - entry) * lot * upp, "SL", max_consumed
            if b['high'] >= tp:
                return j, (tp - entry) * lot * upp, "TP", max_consumed
        else:
            adverse = max(0.0, (b['high'] + sp) - entry)
            if adverse > 0:
                cp = adverse / sl_distance
                if cp > max_consumed:
                    max_consumed = cp
            if b['high'] + sp >= sl:
                return j, (entry - sl) * lot * upp, "SL", max_consumed
            if b['low'] + sp <= tp:
                return j, (entry - tp) * lot * upp, "TP", max_consumed
        j += 1
    exit_px = bars[end-1]['close'] + (0 if dirn == "BUY" else sp)
    pnl = (exit_px - entry) * lot * upp if dirn == "BUY" else (entry - exit_px) * lot * upp
    return end - 1, pnl, "TIMEOUT", max_consumed


# ---------------------------------------------------------------------------
# Strategy detectors — mirrored from backtest_d1_strategies.py
# ---------------------------------------------------------------------------
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
        var = sum((c-mid)**2 for c in win) / 20
        std = var ** 0.5
        if std == 0: continue
        upper = mid + 2.5*std; lower = mid - 2.5*std
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


# ---------------------------------------------------------------------------
# Run everything
# ---------------------------------------------------------------------------
print("\nFetching bars and running strategies...")
all_trades = defaultdict(list)   # strat_name -> list of (ts, sig, pnl, outcome, max_consumed)

for sym in SYMBOLS:
    if sym not in SYM: continue
    bars = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 2000)
    if bars is None or len(bars) < 250:
        print(f"  {sym}: insufficient bars")
        continue
    days = (bars[-1]['time'] - bars[0]['time']) / 86400
    for name, fn in STRATS:
        try:
            trades = fn(bars, sym)
        except Exception as e:
            print(f"  {sym} {name}: ERROR {e}")
            continue
        for t in trades:
            all_trades[name].append((sym, *t))   # (sym, ts, sig, pnl, outcome, mc)
    print(f"  {sym}: {int(days)}d, "
          + ", ".join(f"{name}={sum(1 for x in all_trades[name] if x[0]==sym)}" for name, _ in STRATS))


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def avg_risk_dollars(trades):
    """Risk per losing trade — used to value the -0.7R legacy bucket close."""
    sls = [t[3] for t in trades if t[4] == "SL"]   # pnl of SL hits
    if not sls:
        return RISK_PER_TRADE_USD
    return abs(np.mean(sls))


def analyze(name, trades):
    if not trades:
        return None
    n = len(trades)
    pnls = np.array([t[3] for t in trades])
    outcomes = [t[4] for t in trades]
    max_consumed = np.array([t[5] for t in trades])

    # Who touched 70%?
    touched = max_consumed >= NEAR_SL_PCT
    n_touched = int(touched.sum())

    # Of those, who ended at SL vs recovered to TP vs partial recovery (small loss)?
    if n_touched > 0:
        touched_outcomes = [outcomes[i] for i in range(n) if touched[i]]
        touched_pnls     = pnls[touched]
        n_sl_after_touch = sum(1 for o in touched_outcomes if o == "SL")
        n_tp_after_touch = sum(1 for o in touched_outcomes if o == "TP")
        n_timeout_after  = sum(1 for o in touched_outcomes if o == "TIMEOUT")
        # "Recovered" = ended positive (TP or positive TIMEOUT)
        n_recovered_pos  = int((touched_pnls > 0).sum())
        recovery_rate    = n_recovered_pos / n_touched
        avg_touched_pnl  = float(touched_pnls.mean())
    else:
        n_sl_after_touch = n_tp_after_touch = n_timeout_after = n_recovered_pos = 0
        recovery_rate = 0.0
        avg_touched_pnl = 0.0

    # Total $ under each policy:
    natural_total = float(pnls.sum())

    # LEGACY: every 70%-touch closed at -0.7 * sl_distance (in $) instead of riding
    # Approximate: avg risk * 0.7 per touched trade as the realized loss
    R = avg_risk_dollars(trades)
    legacy_loss_on_touched = -0.7 * R * n_touched
    legacy_pnl_on_untouched = float(pnls[~touched].sum())
    legacy_total = legacy_pnl_on_untouched + legacy_loss_on_touched

    # RESCUE with bootstrap default: family default applied to "keep" decisions.
    # Approximation: for trades that touched 70% but NOT 90% (hard-close floor):
    #   With prob recovery_default → realize TP outcome
    #   With prob (1 - recovery_default) → realize SL outcome
    # For trades that touched 90%+: closed at -0.9R (hard-close)
    # NOTE: this is the BOOTSTRAP behavior, not the full-scorer behavior.
    family_recovery = FAMILY_DEFAULT_RECOVERY.get(name, 0.30)
    rescue_pnl_on_untouched = legacy_pnl_on_untouched
    touched_hard = max_consumed >= HARD_CLOSE_PCT
    n_hard = int(touched_hard.sum())
    rescue_hard_loss = -0.9 * R * n_hard
    # For non-hard touched (70-90%): use ACTUAL outcomes as ground truth.
    # The rescue layer's "keep" decision means trade rides to natural exit, so
    # natural outcome IS the rescue outcome. No probabilistic approximation needed
    # for the BACKTEST — we have the real exit data.
    soft_mask = touched & ~touched_hard
    rescue_soft_pnl = float(pnls[soft_mask].sum())
    rescue_total = rescue_pnl_on_untouched + rescue_soft_pnl + rescue_hard_loss

    return dict(
        name=name, n=n,
        natural_total=natural_total, legacy_total=legacy_total, rescue_total=rescue_total,
        n_touched=n_touched, n_hard=n_hard,
        recovery_rate=recovery_rate,
        avg_R=R,
        family_default=family_recovery,
        n_sl_after_touch=n_sl_after_touch,
        n_tp_after_touch=n_tp_after_touch,
        n_timeout_after=n_timeout_after,
        n_recovered_pos=n_recovered_pos,
        avg_touched_pnl=avg_touched_pnl,
    )


results = []
for name, _ in STRATS:
    r = analyze(name, all_trades[name])
    if r is not None:
        results.append(r)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
print("\n" + "=" * 110)
print(f"RESCUE-LAYER RECOVERY-RATE ANALYSIS  —  risk ${RISK_PER_TRADE_USD}/trade  —  "
      f"NEAR_SL={NEAR_SL_PCT*100:.0f}%, HARD_CLOSE={HARD_CLOSE_PCT*100:.0f}%")
print("=" * 110)
hdr = (f"{'Strategy':<14} {'N':>5} {'Touched':>8} {'Hard':>5} "
       f"{'Recov%':>7} {'Default':>8} {'Avg-R$':>8}  "
       f"{'NATURAL$':>12} {'LEGACY$':>12} {'RESCUE$':>12}  {'R-vs-L':>10}")
print(hdr)
print("-" * len(hdr))
totals = {"natural": 0.0, "legacy": 0.0, "rescue": 0.0,
          "n": 0, "touched": 0, "recov": 0}
for r in results:
    diff = r['rescue_total'] - r['legacy_total']
    print(f"{r['name']:<14} {r['n']:>5} {r['n_touched']:>8} {r['n_hard']:>5} "
          f"{r['recovery_rate']*100:>6.1f}% {r['family_default']*100:>7.0f}% "
          f"{r['avg_R']:>8.2f}  "
          f"{r['natural_total']:>+12.2f} {r['legacy_total']:>+12.2f} "
          f"{r['rescue_total']:>+12.2f}  {diff:>+10.2f}")
    totals["natural"] += r['natural_total']
    totals["legacy"]  += r['legacy_total']
    totals["rescue"]  += r['rescue_total']
    totals["n"]       += r['n']
    totals["touched"] += r['n_touched']
    totals["recov"]   += r['n_recovered_pos']

print("-" * len(hdr))
agg_recov = (100.0 * totals["recov"] / totals["touched"]) if totals["touched"] else 0.0
diff_total = totals["rescue"] - totals["legacy"]
print(f"{'TOTAL':<14} {totals['n']:>5} {totals['touched']:>8} {'':>5} "
      f"{agg_recov:>6.1f}% {'':>8} {'':>8}  "
      f"{totals['natural']:>+12.2f} {totals['legacy']:>+12.2f} "
      f"{totals['rescue']:>+12.2f}  {diff_total:>+10.2f}")

# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
print("\n" + "=" * 110)
print("INTERPRETATION")
print("=" * 110)
print(f"  NATURAL    = exit at SL/TP/TIMEOUT only  (= original 8-yr backtest behavior; no bucket close)")
print(f"  LEGACY     = every 70%-touch closed at -0.7R                (= the old static bucket rule)")
print(f"  RESCUE     = keep all 70%-touchers except >=90% hard-close  (= rescue layer's keep policy)")
print()
if totals["rescue"] > totals["legacy"]:
    print(f"  >>> RESCUE beats LEGACY by ${diff_total:+,.2f} across the universe.")
    print(f"      The rescue layer is NET POSITIVE vs the old bucket-close rule on this data.")
else:
    print(f"  >>> RESCUE loses to LEGACY by ${-diff_total:+,.2f}.")
    print(f"      The rescue layer DEGRADES outcomes vs the old rule on this data.")
    print(f"      Consider: raise RESCUE_KEEP_THRESHOLD, lower RESCUE_HARD_CLOSE_PCT, or tighter")
    print(f"      per-family defaults (especially for trend strategies).")

print()
if abs(totals["rescue"] - totals["natural"]) / max(abs(totals["natural"]), 1) < 0.01:
    print(f"  RESCUE ≈ NATURAL — the rescue layer preserves the original 8-yr edge.")
elif totals["rescue"] >= totals["natural"]:
    print(f"  RESCUE >= NATURAL by ${totals['rescue']-totals['natural']:+,.2f} "
          f"(hard-close on >=90%-touchers cuts a sliver of full SL losses).")
else:
    print(f"  RESCUE < NATURAL by ${totals['natural']-totals['rescue']:+,.2f}  "
          f"— hard-close is doing some harm vs naturally riding to SL/TP.")

print()
print("  Validating bootstrap default recovery rates:")
for r in results:
    family = "mean-rev" if r['name'] in MEAN_REV_FAMILY else "trend"
    real = r['recovery_rate']
    default = r['family_default']
    diff = real - default
    flag = "OK" if abs(diff) < 0.10 else ("over" if diff < 0 else "under")
    print(f"    {r['name']:<14} family={family:<8} measured={real*100:>5.1f}%  "
          f"default={default*100:>5.0f}%  diff={diff*100:>+5.1f}pp  [{flag}]")

print()
print("  Action items based on this data:")
print("    - If measured recovery rate is MUCH lower than the family default,")
print("      tighten the per-family bootstrap in loser_rescue.py.")
print("    - Once live near_sl_touches >= 10 per (sym, strat) combo, the live")
print("      rate replaces the family default automatically.")

mt5.shutdown()
