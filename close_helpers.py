"""
Shared close-request helper used by BOTH the bot (smart bucket close) and the
monitor agent (correlation/stale/emergency closes).

Previously each process had a near-identical `_send_close_request` — that's
two copies of the same MT5-quirk code (filling-mode fallback, retcode handling)
that could drift apart. This module is the single source.
"""

import MetaTrader5 as mt5


# Default deviation for closes — higher than open deviation since we accept
# more slippage when getting out (the cost of waiting could be worse).
DEFAULT_CLOSE_DEVIATION = 300


def send_close_request(position, reason, filling_mode=None, deviation=DEFAULT_CLOSE_DEVIATION):
    """Build and send a single CLOSE request to the broker.

    Args:
        position:      MT5 position object (from mt5.positions_get()).
        reason:        Short tag for the broker comment (e.g. "agent-stale", "bucket-smart").
        filling_mode:  Optional MT5 filling-mode constant. If None or rejected,
                       falls back across FOK / IOC / RETURN automatically.
        deviation:     Max points of slippage accepted.

    Returns:
        (success: bool, realized_pnl: float, ticket: int)
        realized_pnl is computed from the (still-open) position snapshot — it
        reflects the floating P&L the broker realized when closing at market.

    Thread-safe — safe to call concurrently from a ThreadPoolExecutor. Does
    NOT mutate any shared state. Does NOT log; callers handle logging.
    """
    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None:
        return False, 0.0, position.ticket

    is_buy = position.type == mt5.POSITION_TYPE_BUY
    opp_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
    price = tick.bid if is_buy else tick.ask

    req = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       position.symbol,
        "volume":       position.volume,
        "type":         opp_type,
        "position":     position.ticket,
        "price":        price,
        "deviation":    deviation,
        "magic":        position.magic,
        "comment":      f"close-{reason}"[:30],   # broker comment is 32 char limit
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode or mt5.ORDER_FILLING_FOK,
    }
    res = mt5.order_send(req)
    rc = getattr(res, "retcode", None)

    # Filling-mode mismatch (retcode 10030): broker rejected the filling type
    # we requested. Try the alternatives in order.
    if rc == 10030:
        for alt in (mt5.ORDER_FILLING_FOK,
                    mt5.ORDER_FILLING_IOC,
                    mt5.ORDER_FILLING_RETURN):
            req["type_filling"] = alt
            res = mt5.order_send(req)
            if getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                break

    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
        realized = position.profit + position.swap + getattr(position, "commission", 0.0)
        return True, realized, position.ticket
    return False, 0.0, position.ticket
