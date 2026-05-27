"""
D1 Portfolio strategies — pure-function signal generators.

Each signal returns a TradePlan or None when called against a list of D1 bars
ending at the most-recent CLOSED bar (oldest first).
"""

from dataclasses import dataclass
from typing import Optional

from d1_portfolio_config import (BB_PERIOD, BB_STD, DONCHIAN_LOOKBACK,
                                  MIN_LOT, MAX_LOT, MOMENTUM_LOOKBACK,
                                  RSI2_OVERBOUGHT, RSI2_OVERSOLD, STRAT_PARAMS,
                                  TREND_MA_PERIOD, ATR_PERIOD,
                                  RISK_PER_TRADE_USD, RISK_PER_TRADE_PCT,
                                  USE_DYNAMIC_RISK)
from indicators import atr as _ind_atr, sma as _ind_sma, rsi as _ind_rsi, bollinger as _ind_bollinger


LOT_STEP = 0.01

# Globally-set current risk in $ — updated each tick from current equity.
# When USE_DYNAMIC_RISK is True, the bot computes this from equity × PCT/100
# before calling detectors. When False, it stays at RISK_PER_TRADE_USD.
_CURRENT_RISK_USD = RISK_PER_TRADE_USD


def set_current_risk_usd(value: float):
    """Bot calls this each tick to update risk based on live equity."""
    global _CURRENT_RISK_USD
    _CURRENT_RISK_USD = float(value)


def get_current_risk_usd() -> float:
    return _CURRENT_RISK_USD


@dataclass
class TradePlan:
    symbol:     str
    strategy:   str
    direction:  str    # "BUY" or "SELL"
    entry_ref:  float  # reference price (next-bar open is what we'll really get)
    sl:         float
    tp:         float  # 0.0 if dynamic exit (e.g. bb_extreme uses middle band)
    lot:        float
    max_hold_days: int


# ---------------------------------------------------------------------------
# Indicators — thin wrappers around indicators.py for default-period
# convenience. The actual math lives in indicators.py (single source).
# Re-exported names (`atr`, `sma`, `rsi`, `bollinger`) preserve the public API
# so callers in this file and external imports keep working.
# ---------------------------------------------------------------------------
def atr(bars, period=ATR_PERIOD):
    return _ind_atr(bars, period)


def sma(bars, period, field='close'):
    return _ind_sma(bars, period, field)


def rsi(bars, period):
    return _ind_rsi(bars, period)


def bollinger(bars, period, k):
    return _ind_bollinger(bars, period, k)


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------
def normalize_lot(v):
    v = max(MIN_LOT, min(MAX_LOT, v))
    return round(round(v/LOT_STEP)*LOT_STEP, 2)


def size_for_risk(stop_dist, usd_per_pp):
    """Compute lot size for the current per-trade $ risk (dynamic if enabled).
    Returns 0.0 if even MIN_LOT would exceed the allowed risk (safety for tiny accounts)."""
    if stop_dist <= 0 or usd_per_pp <= 0:
        return 0.0
    target_lot = _CURRENT_RISK_USD / (stop_dist * usd_per_pp)
    # If even one MIN_LOT exceeds the budget, refuse the trade
    min_lot_risk = MIN_LOT * stop_dist * usd_per_pp
    if min_lot_risk > _CURRENT_RISK_USD * 1.5:   # allow 50% overage tolerance
        return 0.0
    return normalize_lot(target_lot)


# ---------------------------------------------------------------------------
# Strategy detectors — each returns TradePlan or None
# ---------------------------------------------------------------------------
def detect_donchian20(bars, symbol, usd_per_pp, spread):
    """20-bar D1 Donchian breakout. Enter in direction of breakout next bar open."""
    if len(bars) < DONCHIAN_LOOKBACK + 2:
        return None
    a = atr(bars)
    if a is None or a == 0:
        return None
    recent = bars[-(DONCHIAN_LOOKBACK+1):-1]
    hh = max(r['high'] for r in recent)
    ll = min(r['low']  for r in recent)
    cur = bars[-1]
    sig = None
    if cur['high'] >= hh: sig = "BUY"
    elif cur['low'] <= ll: sig = "SELL"
    if sig is None: return None
    p = STRAT_PARAMS["donchian20"]
    sl_dist = p["sl_atr"] * a
    tp_dist = p["sl_atr"] * a * p["tp_atr_mult"]
    # entry ref is current close; actual fill = next bar open
    entry = cur['close'] + (spread if sig == "BUY" else 0)
    if sig == "BUY":
        sl = entry - sl_dist; tp = entry + tp_dist
    else:
        sl = entry + sl_dist; tp = entry - tp_dist
    lot = size_for_risk(sl_dist, usd_per_pp)
    if lot < MIN_LOT: return None
    return TradePlan(symbol, "donchian20", sig, entry, sl, tp, lot, p["max_hold_d"])


def detect_momentum60(bars, symbol, usd_per_pp, spread):
    """60-day momentum: enter in direction of net 60-day return if |ret| > 2 ATR."""
    if len(bars) < MOMENTUM_LOOKBACK + ATR_PERIOD + 5:
        return None
    a = atr(bars)
    if a is None or a == 0:
        return None
    ret60 = bars[-1]['close'] - bars[-MOMENTUM_LOOKBACK-1]['close']
    if abs(ret60) < 2 * a:
        return None
    sig = "BUY" if ret60 > 0 else "SELL"
    p = STRAT_PARAMS["momentum60"]
    sl_dist = p["sl_atr"] * a
    tp_dist = p["sl_atr"] * a * p["tp_atr_mult"]
    entry = bars[-1]['close'] + (spread if sig == "BUY" else 0)
    if sig == "BUY":
        sl = entry - sl_dist; tp = entry + tp_dist
    else:
        sl = entry + sl_dist; tp = entry - tp_dist
    lot = size_for_risk(sl_dist, usd_per_pp)
    if lot < MIN_LOT: return None
    return TradePlan(symbol, "momentum60", sig, entry, sl, tp, lot, p["max_hold_d"])


def detect_donchian20_T(bars, symbol, usd_per_pp, spread):
    """Tight-TP variant of Donchian20: SL=2×ATR, TP=1.5×SL (vs 3×SL original).
    Higher win rate (40%→48%), smaller wins, same total $/day."""
    if len(bars) < DONCHIAN_LOOKBACK + 2: return None
    a = atr(bars)
    if a is None or a == 0: return None
    recent = bars[-(DONCHIAN_LOOKBACK+1):-1]
    hh = max(r['high'] for r in recent); ll = min(r['low'] for r in recent)
    cur = bars[-1]
    sig = None
    if cur['high'] >= hh: sig = "BUY"
    elif cur['low'] <= ll: sig = "SELL"
    if sig is None: return None
    sl_dist = 2.0 * a
    tp_dist = 1.5 * sl_dist   # tight: 1.5×SL instead of 3×SL
    entry = cur['close'] + (spread if sig == "BUY" else 0)
    if sig == "BUY": sl = entry - sl_dist; tp = entry + tp_dist
    else:            sl = entry + sl_dist; tp = entry - tp_dist
    lot = size_for_risk(sl_dist, usd_per_pp)
    if lot < MIN_LOT: return None
    return TradePlan(symbol, "donchian20_T", sig, entry, sl, tp, lot, 40)


def detect_momentum60_T(bars, symbol, usd_per_pp, spread):
    """Tight-TP variant of Momentum60: SL=2×ATR, TP=1×SL (vs 1.5×SL original).
    Higher win rate (49%→59%), smaller faster wins."""
    if len(bars) < MOMENTUM_LOOKBACK + ATR_PERIOD + 5: return None
    a = atr(bars)
    if a is None or a == 0: return None
    ret60 = bars[-1]['close'] - bars[-MOMENTUM_LOOKBACK-1]['close']
    if abs(ret60) < 2 * a: return None
    sig = "BUY" if ret60 > 0 else "SELL"
    sl_dist = 2.0 * a
    tp_dist = 1.0 * sl_dist   # tight: 1.0×SL instead of 1.5×SL
    entry = bars[-1]['close'] + (spread if sig == "BUY" else 0)
    if sig == "BUY": sl = entry - sl_dist; tp = entry + tp_dist
    else:            sl = entry + sl_dist; tp = entry - tp_dist
    lot = size_for_risk(sl_dist, usd_per_pp)
    if lot < MIN_LOT: return None
    return TradePlan(symbol, "momentum60_T", sig, entry, sl, tp, lot, 60)


def detect_rsi2(bars, symbol, usd_per_pp, spread):
    """Connors RSI(2): oversold + above 200-MA -> BUY; overbought + below 200-MA -> SELL."""
    if len(bars) < TREND_MA_PERIOD + 5:
        return None
    ma200 = sma(bars, TREND_MA_PERIOD)
    r = rsi(bars, 2)
    a = atr(bars)
    if ma200 is None or r is None or a is None or a == 0:
        return None
    c = bars[-1]['close']
    sig = None
    if c > ma200 and r < RSI2_OVERSOLD: sig = "BUY"
    elif c < ma200 and r > RSI2_OVERBOUGHT: sig = "SELL"
    if sig is None: return None
    p = STRAT_PARAMS["rsi2"]
    sl_dist = p["sl_atr"] * a
    tp_dist = sl_dist * p["tp_atr_mult"] * 2  # tp_atr_mult is fraction; *2 because base = sl_atr=2
    entry = c + (spread if sig == "BUY" else 0)
    if sig == "BUY":
        sl = entry - sl_dist; tp = entry + tp_dist
    else:
        sl = entry + sl_dist; tp = entry - tp_dist
    lot = size_for_risk(sl_dist, usd_per_pp)
    if lot < MIN_LOT: return None
    return TradePlan(symbol, "rsi2", sig, entry, sl, tp, lot, p["max_hold_d"])


def detect_3day_reverse(bars, symbol, usd_per_pp, spread):
    """3 consecutive same-direction D1 bars → reverse."""
    if len(bars) < ATR_PERIOD + 5:
        return None
    b1, b2, b3 = bars[-3], bars[-2], bars[-1]
    all_up   = b1['close'] > b1['open'] and b2['close'] > b2['open'] and b3['close'] > b3['open']
    all_down = b1['close'] < b1['open'] and b2['close'] < b2['open'] and b3['close'] < b3['open']
    if not (all_up or all_down): return None
    sig = "SELL" if all_up else "BUY"
    a = atr(bars)
    if a is None or a == 0: return None
    p = STRAT_PARAMS["3day_reverse"]
    sl_dist = p["sl_atr"] * a
    tp_dist = a   # 1×ATR
    entry = b3['close'] + (spread if sig == "BUY" else 0)
    if sig == "BUY":
        sl = entry - sl_dist; tp = entry + tp_dist
    else:
        sl = entry + sl_dist; tp = entry - tp_dist
    lot = size_for_risk(sl_dist, usd_per_pp)
    if lot < MIN_LOT: return None
    return TradePlan(symbol, "3day_reverse", sig, entry, sl, tp, lot, p["max_hold_d"])


def detect_bb_extreme(bars, symbol, usd_per_pp, spread):
    """D1 Bollinger %b > 1.0 or < 0.0 (close outside 2.5-sigma band) → revert to mid."""
    bb = bollinger(bars, BB_PERIOD, BB_STD)
    if bb is None: return None
    upper, mid, lower = bb
    c = bars[-1]['close']
    sig = None
    if c < lower: sig = "BUY"
    elif c > upper: sig = "SELL"
    if sig is None: return None
    a = atr(bars)
    if a is None or a == 0: return None
    p = STRAT_PARAMS["bb_extreme"]
    sl_dist = p["sl_atr"] * a
    entry = c + (spread if sig == "BUY" else 0)
    if sig == "BUY":
        sl = entry - sl_dist; tp = mid
    else:
        sl = entry + sl_dist; tp = mid
    if abs(tp - entry) < 0.5 * a:
        return None   # target too close after sizing
    lot = size_for_risk(sl_dist, usd_per_pp)
    if lot < MIN_LOT: return None
    return TradePlan(symbol, "bb_extreme", sig, entry, sl, tp, lot, p["max_hold_d"])


# ===========================================================================
# H1-timeframe versions of winning strategies (faster trade closure: 4-48h)
# Same logic, different timeframe — bot fetches H1 bars instead of D1.
# Strategy NAME has _H1 suffix and indicates max_hold_d is in HOURS not days.
# ===========================================================================
def detect_consensus(bars, symbol, usd_per_pp, spread):
    """High-confidence confirmation: fires only when at least one HIGH-WR strategy
    (rsi2 or 3day_reverse) AND at least one TREND strategy (donchian20/momentum60/bb_extreme)
    point the same direction on this bar. Combines mean-reversion + trend confluence.

    Backtest: +36% $/day improvement over individual strategies (v14 test on 20 symbols D1).
    Win rate ~60% but with materially better win/loss ratio."""
    if len(bars) < TREND_MA_PERIOD + 5: return None

    sigs = {}
    # rsi2 (high WR)
    ma200 = sma(bars, TREND_MA_PERIOD)
    r2 = rsi(bars, 2)
    if ma200 is not None and r2 is not None:
        c = bars[-1]['close']
        if c > ma200 and r2 < RSI2_OVERSOLD:    sigs["rsi2"] = "BUY"
        elif c < ma200 and r2 > RSI2_OVERBOUGHT: sigs["rsi2"] = "SELL"
    # 3day_reverse (high WR)
    if len(bars) >= 3:
        b1, b2, b3 = bars[-3], bars[-2], bars[-1]
        if b1['close']>b1['open'] and b2['close']>b2['open'] and b3['close']>b3['open']:
            sigs["3day_reverse"] = "SELL"
        elif b1['close']<b1['open'] and b2['close']<b2['open'] and b3['close']<b3['open']:
            sigs["3day_reverse"] = "BUY"
    # donchian20 (trend)
    if len(bars) >= DONCHIAN_LOOKBACK + 1:
        recent = bars[-(DONCHIAN_LOOKBACK+1):-1]
        hh = max(r['high'] for r in recent); ll = min(r['low'] for r in recent)
        cur = bars[-1]
        if cur['high'] >= hh: sigs["donchian20"] = "BUY"
        elif cur['low'] <= ll: sigs["donchian20"] = "SELL"
    # momentum60 (trend)
    if len(bars) >= MOMENTUM_LOOKBACK + ATR_PERIOD + 5:
        a = atr(bars)
        if a is not None and a > 0:
            ret60 = bars[-1]['close'] - bars[-MOMENTUM_LOOKBACK-1]['close']
            if abs(ret60) >= 2 * a:
                sigs["momentum60"] = "BUY" if ret60 > 0 else "SELL"
    # bb_extreme (trend at boundary)
    bb = bollinger(bars, BB_PERIOD, BB_STD)
    if bb is not None:
        upper, mid, lower = bb
        c = bars[-1]['close']
        if c < lower: sigs["bb_extreme"] = "BUY"
        elif c > upper: sigs["bb_extreme"] = "SELL"

    if not sigs: return None

    HIGH_WR = {"rsi2", "3day_reverse"}
    # find the dominant direction
    buys  = [k for k, v in sigs.items() if v == "BUY"]
    sells = [k for k, v in sigs.items() if v == "SELL"]
    group = buys if len(buys) > len(sells) else (sells if sells else [])
    if len(group) < 2:
        return None
    has_high  = any(k in HIGH_WR for k in group)
    has_other = any(k not in HIGH_WR for k in group)
    if not (has_high and has_other):
        return None

    sig = "BUY" if group is buys else "SELL"
    a = atr(bars)
    if a is None or a == 0: return None

    # Use averaged stop/target across the agreeing strategies
    sl_dist = 1.75 * a   # average of (1.5 for high-WR, 2.0 for trend)
    tp_dist = 1.5 * a    # conservative blended target
    entry = bars[-1]['close'] + (spread if sig == "BUY" else 0)
    if sig == "BUY": sl = entry - sl_dist; tp = entry + tp_dist
    else:            sl = entry + sl_dist; tp = entry - tp_dist
    lot = size_for_risk(sl_dist, usd_per_pp)
    if lot < MIN_LOT: return None
    return TradePlan(symbol, "consensus", sig, entry, sl, tp, lot, 20)


def detect_donchian20_H1(bars, symbol, usd_per_pp, spread):
    """H1 Donchian breakout — same logic as D1 but on H1 bars. Hold up to 48 hours."""
    if len(bars) < DONCHIAN_LOOKBACK + 2: return None
    a = atr(bars)
    if a is None or a == 0: return None
    recent = bars[-(DONCHIAN_LOOKBACK+1):-1]
    hh = max(r['high'] for r in recent); ll = min(r['low'] for r in recent)
    cur = bars[-1]
    sig = None
    if cur['high'] >= hh: sig = "BUY"
    elif cur['low'] <= ll: sig = "SELL"
    if sig is None: return None
    sl_dist = 2.0 * a
    tp_dist = 3.0 * sl_dist   # 3:1 R:R same as D1 (data confirms this works on H1 too)
    entry = cur['close'] + (spread if sig == "BUY" else 0)
    if sig == "BUY": sl = entry - sl_dist; tp = entry + tp_dist
    else:            sl = entry + sl_dist; tp = entry - tp_dist
    lot = size_for_risk(sl_dist, usd_per_pp)
    if lot < MIN_LOT: return None
    return TradePlan(symbol, "donchian20_H1", sig, entry, sl, tp, lot, 48)

def detect_momentum60_H1(bars, symbol, usd_per_pp, spread):
    """H1 60-period momentum. Same as D1 logic, hold up to 48 hours."""
    if len(bars) < MOMENTUM_LOOKBACK + ATR_PERIOD + 5: return None
    a = atr(bars)
    if a is None or a == 0: return None
    ret60 = bars[-1]['close'] - bars[-MOMENTUM_LOOKBACK-1]['close']
    if abs(ret60) < 2 * a: return None
    sig = "BUY" if ret60 > 0 else "SELL"
    sl_dist = 2.0 * a
    tp_dist = 1.5 * sl_dist
    entry = bars[-1]['close'] + (spread if sig == "BUY" else 0)
    if sig == "BUY": sl = entry - sl_dist; tp = entry + tp_dist
    else:            sl = entry + sl_dist; tp = entry - tp_dist
    lot = size_for_risk(sl_dist, usd_per_pp)
    if lot < MIN_LOT: return None
    return TradePlan(symbol, "momentum60_H1", sig, entry, sl, tp, lot, 48)

def detect_rsi2_H1(bars, symbol, usd_per_pp, spread):
    """H1 Connors RSI(2). Same as D1, hold up to 24 hours."""
    if len(bars) < TREND_MA_PERIOD + 5: return None
    ma200 = sma(bars, TREND_MA_PERIOD)
    r = rsi(bars, 2)
    a = atr(bars)
    if ma200 is None or r is None or a is None or a == 0: return None
    c = bars[-1]['close']
    sig = None
    if c > ma200 and r < RSI2_OVERSOLD: sig = "BUY"
    elif c < ma200 and r > RSI2_OVERBOUGHT: sig = "SELL"
    if sig is None: return None
    sl_dist = 2.0 * a
    tp_dist = 1.5 * a
    entry = c + (spread if sig == "BUY" else 0)
    if sig == "BUY": sl = entry - sl_dist; tp = entry + tp_dist
    else:            sl = entry + sl_dist; tp = entry - tp_dist
    lot = size_for_risk(sl_dist, usd_per_pp)
    if lot < MIN_LOT: return None
    return TradePlan(symbol, "rsi2_H1", sig, entry, sl, tp, lot, 24)

def detect_bb_extreme_H1(bars, symbol, usd_per_pp, spread):
    """H1 Bollinger extreme. Same as D1, hold up to 24 hours."""
    bb = bollinger(bars, BB_PERIOD, BB_STD)
    if bb is None: return None
    upper, mid, lower = bb
    c = bars[-1]['close']
    sig = None
    if c < lower: sig = "BUY"
    elif c > upper: sig = "SELL"
    if sig is None: return None
    a = atr(bars)
    if a is None or a == 0: return None
    sl_dist = 1.5 * a
    entry = c + (spread if sig == "BUY" else 0)
    if sig == "BUY": sl = entry - sl_dist; tp = mid
    else:            sl = entry + sl_dist; tp = mid
    if abs(tp - entry) < 0.5 * a: return None
    lot = size_for_risk(sl_dist, usd_per_pp)
    if lot < MIN_LOT: return None
    return TradePlan(symbol, "bb_extreme_H1", sig, entry, sl, tp, lot, 24)


# Strategy registry
STRATEGY_DETECTORS = {
    "donchian20":     detect_donchian20,
    "momentum60":     detect_momentum60,
    "donchian20_T":   detect_donchian20_T,
    "momentum60_T":   detect_momentum60_T,
    "rsi2":           detect_rsi2,
    "3day_reverse":   detect_3day_reverse,
    "bb_extreme":     detect_bb_extreme,
    "consensus":      detect_consensus,   # NEW: high-quality multi-strategy confirmation
    # H1 variants for faster trade closure (intraday)
    "donchian20_H1":  detect_donchian20_H1,
    "momentum60_H1":  detect_momentum60_H1,
    "rsi2_H1":        detect_rsi2_H1,
    "bb_extreme_H1":  detect_bb_extreme_H1,
}

# Helper: which timeframe does a strategy run on?
def strategy_timeframe(strat_name):
    """Return MT5 timeframe constant for a strategy."""
    import MetaTrader5 as mt5
    if strat_name.endswith("_H1"):
        return mt5.TIMEFRAME_H1
    return mt5.TIMEFRAME_D1
