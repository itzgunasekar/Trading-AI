"""
Indicator functions — single source of truth.

Used by:
  - d1_portfolio_strategy.py     (live detectors)
  - trade_intelligence.py        (quality scorer's regime sub-score)
  - loser_rescue.py              (regime sub-score)
  - backtests/*                  (all four rescue/strategy backtests)

All functions take a sequence of bars (numpy structured array or list of dicts
with 'high', 'low', 'close', 'open' keys — both work) and an index (or
implicitly use the tail for the live API).

TWO API STYLES (both supported for backward compatibility):

  • TAIL-WINDOW API (live code):  atr(bars, period=14)
      The function uses bars[-period-1:] implicitly. Caller passes the
      whole bar list and lets the function read the tail.

  • INDEX API (backtests):        atr_at(bars, idx, period=14)
      The function computes the indicator value AT bar index `idx` using
      bars[idx-period:idx]. Used to step through history.
"""

# ---------------------------------------------------------------------------
# ATR (Average True Range)
# ---------------------------------------------------------------------------
def atr(bars, period=14):
    """Tail-window ATR. Returns None if not enough bars."""
    if bars is None or len(bars) < period + 1:
        return None
    trs = []
    for k in range(len(bars) - period, len(bars)):
        h  = bars[k]['high']
        l  = bars[k]['low']
        pc = bars[k-1]['close']
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / period


def atr_at(bars, idx, period=14):
    """ATR value at bar index `idx` (uses bars[idx-period:idx])."""
    if idx < period + 1:
        return None
    trs = []
    for k in range(idx - period, idx):
        h  = bars[k]['high']
        l  = bars[k]['low']
        pc = bars[k-1]['close']
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / period


# ---------------------------------------------------------------------------
# SMA (Simple Moving Average)
# ---------------------------------------------------------------------------
def sma(bars, period, field='close'):
    """Tail-window SMA of `field` over the last `period` bars."""
    if bars is None or len(bars) < period:
        return None
    return sum(bars[k][field] for k in range(len(bars) - period, len(bars))) / period


def sma_at(bars, idx, period, field='close'):
    """SMA at bar index `idx`."""
    if idx < period:
        return None
    return sum(bars[k][field] for k in range(idx - period + 1, idx + 1)) / period


# ---------------------------------------------------------------------------
# RSI (Wilder-style, matches the rest of the project)
# ---------------------------------------------------------------------------
def rsi(bars, period):
    """Tail-window RSI on closes."""
    if bars is None or len(bars) < period + 1:
        return None
    closes = [bars[k]['close'] for k in range(len(bars) - period - 1, len(bars))]
    g = l = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        if d >= 0: g += d
        else:      l -= d
    ag, al = g / period, l / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def rsi_at(bars, idx, period):
    """RSI at bar index `idx` (uses closes[idx-period:idx+1])."""
    if idx < period + 1:
        return None
    closes = [bars[k]['close'] for k in range(idx - period, idx + 1)]
    g = l = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        if d >= 0: g += d
        else:      l -= d
    ag, al = g / period, l / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------
def bollinger(bars, period, k):
    """Tail-window Bollinger Bands. Returns (upper, mid, lower) or None."""
    if bars is None or len(bars) < period:
        return None
    win = [bars[i]['close'] for i in range(len(bars) - period, len(bars))]
    mid = sum(win) / period
    var = sum((c - mid) ** 2 for c in win) / period
    std = var ** 0.5
    return mid + k * std, mid, mid - k * std


def bollinger_at(bars, idx, period, k):
    """Bollinger Bands at bar index `idx`."""
    if idx < period - 1:
        return None
    win = [bars[i]['close'] for i in range(idx - period + 1, idx + 1)]
    mid = sum(win) / period
    var = sum((c - mid) ** 2 for c in win) / period
    std = var ** 0.5
    return mid + k * std, mid, mid - k * std


# ---------------------------------------------------------------------------
# EMA (used by trade_intelligence MTF alignment score)
# ---------------------------------------------------------------------------
def ema(values, period):
    """Exponential moving average of a flat list of values."""
    if values is None or len(values) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e
