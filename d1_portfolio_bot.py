"""
D1 Portfolio Bot — multi-symbol, multi-strategy daily-timeframe runner.

Architecture:
  - Polls every POLL_SECONDS (default 60s)
  - Tracks the most recent CLOSED D1 bar per symbol
  - When a NEW D1 bar closes, runs all configured (symbol, strategy) detectors
  - For any detector returning a TradePlan: opens position with server-side SL+TP
  - Each (symbol, strategy) gets a unique MAGIC so positions never collide
  - Account-level: max simultaneous positions, daily loss cap, global DD cap
  - State recovery: existing positions matched by MAGIC are adopted on startup

This is the bot version of the 31 surviving combinations from backtest_v9.
"""

import sys
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import MetaTrader5 as mt5

# Thread pool reused for parallel close orders — sized for up to 32 simultaneous closes
_CLOSE_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="bot-close")

# Files written by monitor_agent.py — bot reads them to honor risk/news signals.
RISK_MULT_FILE_PATH = "risk_multiplier.txt"
NEWS_BLACKOUT_FILE_PATH = "news_blackout.flag"


def read_risk_multiplier():
    """Read 0.0–1.0 multiplier set by the monitor agent (drawdown-adaptive).
    Returns 1.0 if file missing or unreadable."""
    try:
        if not os.path.exists(RISK_MULT_FILE_PATH):
            return 1.0
        with open(RISK_MULT_FILE_PATH) as f:
            v = float(f.readline().strip())
            return max(0.0, min(1.0, v))
    except Exception:
        return 1.0


def is_news_blackout():
    """True if the monitor agent has flagged a news window."""
    return os.path.exists(NEWS_BLACKOUT_FILE_PATH)


COOLDOWN_FILE_PATH = "strategy_cooldown.json"

def is_combo_in_cooldown(symbol, strategy):
    """True if the monitor agent recently force-closed this (sym, strat) combo.
    Prevents bot↔agent churn — agent closes, bot won't reopen for N hours."""
    if not os.path.exists(COOLDOWN_FILE_PATH):
        return False
    try:
        import json as _json
        with open(COOLDOWN_FILE_PATH) as f:
            data = _json.load(f)
        key = f"{symbol}|{strategy}"
        exp = data.get(key, 0)
        return exp > datetime.now(timezone.utc).timestamp()
    except Exception:
        return False

from d1_portfolio_config import (ACCT_NO, ACTIVE_COMBINATIONS,
                                  BUCKET_CLOSE_LOSER_NEAR_SL_PCT,
                                  BUCKET_COOLDOWN_SEC,
                                  BUCKET_TP_EQUITY_PCT, BUCKET_TP_MAX,
                                  BUCKET_TP_MIN, BUCKET_TP_MODE,
                                  BUCKET_TP_PER_POS, BUCKET_TP_USD,
                                  DEVIATION_OPEN, EXP_DATE, MAGIC_BASE,
                                  MAX_DAILY_LOSS_PCT, MAX_DAILY_LOSS_USD,
                                  MAX_OPEN_POSITIONS, MAX_POSITIONS_PER_SYMBOL,
                                  MAX_TOTAL_DD_PCT, MAX_TRADES_OPENED_PER_DAY,
                                  POLL_SECONDS,
                                  RISK_MULTIPLIER_BY_SYMBOL,
                                  RISK_PER_TRADE_PCT, RISK_PER_TRADE_USD,
                                  USE_BUCKET_TP, USE_DYNAMIC_RISK,
                                  USE_SMART_BUCKET_CLOSE, VERBOSE)


def save_mfe_mae():
    """Persist MFE/MAE state to disk so restarts don't lose 70%-touch markers
    and recovery tracking. Mirrors the strategy_health.json pattern."""
    try:
        import json as _json
        with open(MFE_MAE_FILE, "w") as f:
            # ticket keys must be strings in JSON
            payload = {str(k): v for k, v in state["mfe_mae"].items()}
            _json.dump(payload, f, indent=2, default=str)
    except Exception as e:
        print(f"[mfe-save err] {e}")


def load_mfe_mae():
    """Load MFE/MAE state from disk on startup. Silently empty if file missing."""
    try:
        import json as _json
        if not os.path.exists(MFE_MAE_FILE):
            return {}
        with open(MFE_MAE_FILE) as f:
            raw = _json.load(f)
        # Convert string keys back to int tickets
        out = {}
        for k, v in raw.items():
            try:
                out[int(k)] = v
            except (TypeError, ValueError):
                continue
        return out
    except Exception:
        return {}


def compute_bucket_target(open_positions_count, equity):
    """Compute current bucket TP target based on configured mode.
    Returns the $ threshold at which the bot will sweep-close all positions."""
    if BUCKET_TP_MODE == "fixed":
        return BUCKET_TP_USD
    if BUCKET_TP_MODE == "per_position":
        raw = open_positions_count * BUCKET_TP_PER_POS
        return max(BUCKET_TP_MIN, min(BUCKET_TP_MAX, raw))
    if BUCKET_TP_MODE == "pct_equity":
        raw = equity * BUCKET_TP_EQUITY_PCT / 100.0
        return max(BUCKET_TP_MIN, min(BUCKET_TP_MAX, raw))
    # fallback
    return BUCKET_TP_USD
from d1_portfolio_strategy import (STRATEGY_DETECTORS, set_current_risk_usd,
                                    get_current_risk_usd, strategy_timeframe)
import trade_intelligence as ti
from d1_portfolio_config import (QUALITY_THRESHOLD, QUALITY_VERBOSE,
                                  USE_QUALITY_FILTER, USE_SL_MIGRATION,
                                  USE_LOSER_RESCUE,
                                  BOT_COOLDOWN_AFTER_CLOSE_HOURS)
import strategy_health as health
import loser_rescue as rescue
import peak_equity_store as peak_store
from close_helpers import send_close_request as _shared_send_close


# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------
state = {
    "filling_mode":    None,
    "last_bar_close":  {},   # (symbol, tf_const) -> ts of last close we processed
    "day":             None,
    "day_pnl":         0.0,
    "day_trades_opened": 0,  # count of new positions opened today (for daily cap)
    "peak_equity":     0.0,
    "sym_info":        {},   # symbol -> {pip, spread, usd_per_pp, digits}
    "last_bucket_fire_ts": 0,
    "health":          {},                  # loaded strategy health data
    "tracked_tickets": {},                  # ticket -> (symbol, strategy) for tracking closures
    "day_pnl_counted_tickets": set(),       # tickets whose P&L already added to day_pnl (prevents double-count)
    "market_closed_until": {},              # symbol -> ts when we can retry (off-market cooldown)
    "mfe_mae":         {},                  # ticket -> {mfe_price, mae_price, first_70pct_ts, opened_ts, symbol, strat}
    "last_mfe_save_ts": 0,                  # last time mfe_mae.json was flushed
}

MFE_MAE_FILE = "mfe_mae.json"
MFE_SAVE_INTERVAL_SEC = 60

# Off-market cooldown — when broker returns retcode 10017 (trade disabled),
# skip new orders for this symbol for 1 hour. Auto-retries at next H1 close.
OFF_MARKET_COOLDOWN_SEC = 3600

_log_last = {}
def log_once(key, msg):
    if _log_last.get(key) != msg:
        print(msg); _log_last[key] = msg


# ---------------------------------------------------------------------------
# Magic encoding so each (symbol, strategy) is uniquely identifiable
# ---------------------------------------------------------------------------
STRAT_IDX = {"donchian20":0, "momentum60":1, "rsi2":2, "3day_reverse":3, "bb_extreme":4,
             "donchian20_T":5, "momentum60_T":6,
             "donchian20_H1":7, "momentum60_H1":8, "rsi2_H1":9, "bb_extreme_H1":10,
             "consensus":11}
SYM_LIST = sorted({s for s, _ in ACTIVE_COMBINATIONS})
SYM_IDX = {s: i for i, s in enumerate(SYM_LIST)}

def magic_for(sym, strat):
    return MAGIC_BASE + 1000 * SYM_IDX[sym] + STRAT_IDX[strat]

def parse_magic(m):
    rel = m - MAGIC_BASE
    if rel < 0: return None, None
    s_idx = rel // 1000; t_idx = rel % 1000
    if s_idx >= len(SYM_LIST) or t_idx >= len(STRAT_IDX): return None, None
    sym = SYM_LIST[s_idx]
    strat = next(k for k, v in STRAT_IDX.items() if v == t_idx)
    return sym, strat


# ---------------------------------------------------------------------------
# MT5 helpers
# ---------------------------------------------------------------------------
def resolve_filling_mode(symbol):
    info = mt5.symbol_info(symbol)
    fm = getattr(info, "filling_mode", 0) or 0
    if fm & 1: return mt5.ORDER_FILLING_FOK
    if fm & 2: return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN

def cache_symbol_info(symbol):
    if symbol in state["sym_info"]: return
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    if info is None: return
    pip = 10 * info.trade_tick_size
    # CRITICAL: use mt5.order_calc_profit for accurate $/lot/$1 calc.
    # The naive formula `trade_tick_value / trade_tick_size` is WRONG for
    # XAUUSD/XAGUSD (10× too low) because MT5 reports tick_value in a
    # broker-specific unit, not always plain USD.
    # We ask the broker directly: what's the $ loss for a 1.0 lot, $1 adverse move?
    tick = mt5.symbol_info_tick(symbol)
    usd_per_pp = info.trade_contract_size   # fallback
    if tick is not None:
        try:
            # SELL 1 lot at price P, then close at P+1 → loss is negative
            p = mt5.order_calc_profit(mt5.ORDER_TYPE_SELL, symbol, 1.0,
                                       tick.bid + 1.0, tick.bid)
            if p is not None and p != 0:
                usd_per_pp = abs(p)
        except Exception:
            pass
    if symbol in ("XAUUSD",): spread = 0.30
    elif symbol in ("XAGUSD",): spread = 0.03
    elif symbol in ("US30","US500","UK100"): spread = 0.50
    else: spread = 1.5 * pip
    state["sym_info"][symbol] = dict(
        pip=pip, spread=spread, usd_per_pp=usd_per_pp,
        digits=info.digits, info=info,
    )

def get_tick(symbol):
    t = mt5.symbol_info_tick(symbol)
    return (float(t.ask), float(t.bid)) if t else (0.0, 0.0)

def closed_d1_bars(symbol, n=210):
    return closed_bars(symbol, mt5.TIMEFRAME_D1, n)

def closed_bars(symbol, tf, n=210):
    """Return last n CLOSED bars (drops the currently-forming bar)."""
    bars = mt5.copy_rates_from_pos(symbol, tf, 0, n + 1)
    if bars is None or len(bars) < n + 1:
        return None
    return bars[:-1]

def my_positions(symbol=None):
    """Return positions opened by this bot (matched by MAGIC range OR by comment
    containing a known strategy name — survives config changes)."""
    pos = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    out = []
    known_strats = set(STRAT_IDX.keys())
    for p in (pos or []):
        is_ours = False
        # Match by magic range
        if p.magic >= MAGIC_BASE and p.magic < MAGIC_BASE + 100000:
            is_ours = True
        # Or by comment matching a strategy name
        elif p.comment and p.comment.strip() in known_strats:
            is_ours = True
        if is_ours and (symbol is None or p.symbol == symbol):
            out.append(p)
    return out


def position_strategy(p):
    """Identify the strategy a position was opened with.
    Prefers the comment (stable across config changes); falls back to magic decoding."""
    if p.comment:
        c = p.comment.strip()
        if c in STRAT_IDX:
            return c
    sym, strat = parse_magic(p.magic)
    return strat


# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------
def send_market_with_sltp(plan) -> bool:
    if plan.symbol not in state["sym_info"]:
        cache_symbol_info(plan.symbol)
    info = state["sym_info"][plan.symbol]
    digits = info["digits"]
    sl = round(plan.sl, digits)
    tp = round(plan.tp, digits) if plan.tp > 0 else 0.0
    ot = mt5.ORDER_TYPE_BUY if plan.direction == "BUY" else mt5.ORDER_TYPE_SELL
    ask, bid = get_tick(plan.symbol)
    px = ask if plan.direction == "BUY" else bid
    if px == 0: return False

    magic = magic_for(plan.symbol, plan.strategy)
    req = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       plan.symbol,
        "volume":       float(plan.lot),
        "type":         ot,
        "price":        px,
        "sl":           sl,
        "tp":           tp,
        "deviation":    DEVIATION_OPEN,
        "magic":        magic,
        "comment":      f"{plan.strategy}",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": state["filling_mode"] or mt5.ORDER_FILLING_FOK,
    }
    res = mt5.order_send(req)
    rc = getattr(res, "retcode", None)
    if rc == 10030:
        for alt in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN):
            req["type_filling"] = alt
            res = mt5.order_send(req)
            if getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                state["filling_mode"] = alt; break
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        final_rc = getattr(res, "retcode", None)
        # Retcode 10017 = "Trade is disabled" — typically off-market hours.
        # Set a 1-hour cooldown so we stop spamming the broker.
        if final_rc == 10017:
            cooldown_until = int(datetime.now(timezone.utc).timestamp()) + OFF_MARKET_COOLDOWN_SEC
            state["market_closed_until"][plan.symbol] = cooldown_until
            log_once(f"offmkt_{plan.symbol}",
                     f"[off-market] {plan.symbol} broker rejected (10017) — "
                     f"skipping for 1h until {datetime.fromtimestamp(cooldown_until, tz=timezone.utc).strftime('%H:%M UTC')}")
        else:
            print(f"[order_send fail] {plan.symbol} {plan.strategy} retcode={final_rc}")
        return False

    risk_usd = abs(px - sl) * plan.lot * info["usd_per_pp"]
    print(f">>> {plan.symbol:<7} {plan.strategy:<13} {plan.direction} {plan.lot:>6.2f} @ {px:.{digits}f}  "
          f"SL={sl:.{digits}f}  TP={tp:.{digits}f}  risk=${risk_usd:.2f}  magic={magic}")
    return True


# ---------------------------------------------------------------------------
# Tick
# ---------------------------------------------------------------------------
def today_utc(): return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def reset_day_if_needed():
    t = today_utc()
    if state["day"] != t:
        state["day"] = t
        state["day_pnl"] = 0.0
        state["day_trades_opened"] = 0
        state["day_pnl_counted_tickets"] = set()   # fresh set for new day
        log_once("reset", f"[day-reset] {t}")

def floating_pnl():
    """Sum of unrealized profit + swap across all bot positions."""
    total = 0.0
    for p in my_positions():
        total += p.profit + p.swap + getattr(p, "commission", 0.0)
    return total


def _send_close_request(position, reason):
    """Bot-side wrapper around the shared close helper.
    Returns (success, realized_pnl) — drops the ticket field for backward
    compatibility with existing call sites in this module."""
    ok, pnl, ticket = _shared_send_close(position, reason, filling_mode=state["filling_mode"])
    if not ok:
        print(f"[close fail] {position.symbol} #{ticket}")
    return ok, pnl


def close_all_bot_positions(reason="bucket"):
    """Fire close orders for EVERY bot position IN PARALLEL.
    All requests hit the broker concurrently — total latency = one round-trip,
    not N × round-trip. Eliminates the slippage window of sequential closes.
    Returns (count_closed, total_realized_pnl)."""
    positions = my_positions()
    if not positions:
        return 0, 0.0
    t0 = time.perf_counter()
    futures = [_CLOSE_POOL.submit(_send_close_request, p, reason) for p in positions]
    closed = 0
    realized = 0.0
    for fut in as_completed(futures):
        ok, pnl = fut.result()
        if ok:
            closed += 1
            realized += pnl
    dt_ms = (time.perf_counter() - t0) * 1000
    print(f">>> PARALLEL CLOSE: {closed}/{len(positions)} positions in {dt_ms:.1f}ms")

    # Quick retry pass for any leftovers (rare — e.g. broker rejected for price-moved)
    remaining = my_positions()
    if remaining:
        print(f"    retrying {len(remaining)} leftover position(s)...")
        futures = [_CLOSE_POOL.submit(_send_close_request, p, reason) for p in remaining]
        for fut in as_completed(futures):
            ok, pnl = fut.result()
            if ok:
                closed += 1
                realized += pnl
    return closed, realized


def _consumed_pct_for_position(p):
    """Return (consumed_pct, current_price) for a loser position, or (None, None)
    if SL/price isn't usable. consumed_pct is % of SL distance the price has
    moved against entry (0 = unchanged, 100 = at SL).

    CRITICAL: uses the ORIGINAL SL captured by the MFE/MAE tracker when the
    position was first seen. Reason: trade_intelligence.migrate_position_stops
    moves SL to break-even+buffer when a trade is 50% to TP. If we used the
    LIVE position.sl after that migration, a BE-protected winner that came
    back to entry would compute as ~100% consumed and get force-closed near
    breakeven — exactly the wrong action. We always measure consumed_pct
    against the trade's intended risk window."""
    if p.price_open == 0:
        return None, None
    # Prefer original SL from the MFE/MAE tracker; fall back to live SL
    tracked = state["mfe_mae"].get(p.ticket)
    original_sl = (tracked or {}).get("original_sl", 0.0)
    sl_to_use = original_sl if original_sl > 0 else p.sl
    if sl_to_use == 0:
        return None, None
    sl_distance = abs(p.price_open - sl_to_use)
    if sl_distance == 0:
        return None, None
    tick = mt5.symbol_info_tick(p.symbol)
    if tick is None:
        return None, None
    if p.type == mt5.POSITION_TYPE_BUY:
        current = tick.bid
        adverse_distance = max(0, p.price_open - current)
    else:
        current = tick.ask
        adverse_distance = max(0, current - p.price_open)
    return 100.0 * adverse_distance / sl_distance, current


def _loser_should_close(p, consumed_pct):
    """Decide whether a near-SL loser is included in the closeable set.

    With USE_LOSER_RESCUE = True, runs the rescue scorer and respects its verdict.
    With False, falls back to the legacy static BUCKET_CLOSE_LOSER_NEAR_SL_PCT rule.

    Returns (should_close: bool, decision_dict_or_None)."""
    if consumed_pct < BUCKET_CLOSE_LOSER_NEAR_SL_PCT:
        return False, None
    if not USE_LOSER_RESCUE:
        return True, None
    decision = rescue.evaluate(p, consumed_pct, state["health"], state["mfe_mae"])
    return decision["action"] == "close", decision


def closeable_floating_pnl():
    """Return the floating P&L of positions that WOULD be closed by smart_bucket_close.
    Used as the bucket-TP trigger: when this subset hits target, fire the smart close
    (which leaves mild losers AND rescue-kept losers open). This lets the bot capture
    profit even when a deep loser drags total floating down.

    Returns (closeable_pnl, n_closeable, n_kept)."""
    positions = my_positions()
    if not positions:
        return 0.0, 0, 0
    closeable_pnl = 0.0
    n_closeable = 0
    n_kept = 0
    for p in positions:
        pnl = p.profit + p.swap + getattr(p, "commission", 0.0)
        if pnl > 0:
            closeable_pnl += pnl
            n_closeable += 1
            continue
        consumed_pct, _ = _consumed_pct_for_position(p)
        if consumed_pct is None:
            n_kept += 1
            continue
        should_close, _ = _loser_should_close(p, consumed_pct)
        if should_close:
            closeable_pnl += pnl
            n_closeable += 1
        else:
            n_kept += 1
    return closeable_pnl, n_closeable, n_kept


def _write_bot_cooldown(closed_combos):
    """Append cooldown entries to strategy_cooldown.json so the bot won't
    immediately reopen the same (sym, strat) at the next bar close after a
    smart bucket sweep. Uses the same JSON file as the monitor agent's
    cooldown writer — last-writer-wins with a longer expiry."""
    if not closed_combos:
        return
    import json as _json
    expires = datetime.now(timezone.utc).timestamp() + BOT_COOLDOWN_AFTER_CLOSE_HOURS * 3600
    try:
        existing = {}
        if os.path.exists(COOLDOWN_FILE_PATH):
            with open(COOLDOWN_FILE_PATH) as f:
                existing = _json.load(f)
        for sym, strat in closed_combos:
            key = f"{sym}|{strat}"
            # Don't shorten an existing (longer) cooldown set by the agent
            existing[key] = max(existing.get(key, 0), expires)
        # Clean expired entries opportunistically
        now = datetime.now(timezone.utc).timestamp()
        existing = {k: v for k, v in existing.items() if v > now}
        with open(COOLDOWN_FILE_PATH, "w") as f:
            _json.dump(existing, f, indent=2)
    except Exception as e:
        print(f"[bot-cooldown write err] {e}")


def smart_bucket_close(reason="bucket-smart"):
    """Smart bucket close — close winners + losers near SL, leave mild losers open.
    When USE_LOSER_RESCUE is True, near-SL losers go through the rescue scorer
    and may be KEPT if the recovery-probability score is high enough.
    Returns (closed_count, realized_pnl, kept_count)."""
    positions = my_positions()
    if not positions:
        return 0, 0.0, 0
    to_close = []
    kept = 0
    for p in positions:
        pnl = p.profit + p.swap + getattr(p, "commission", 0.0)
        if pnl > 0:
            to_close.append((p, "winner"))
            continue
        consumed_pct, _ = _consumed_pct_for_position(p)
        if consumed_pct is None:
            kept += 1
            continue
        should_close, decision = _loser_should_close(p, consumed_pct)
        if decision is not None:
            try:
                rescue.log_decision(p, consumed_pct, decision)
            except Exception as e:
                print(f"[rescue-log err] {e}")
        if should_close:
            tag = (f"loser-{consumed_pct:.0f}%SL"
                   if decision is None
                   else f"loser-{consumed_pct:.0f}%SL-score{decision['score']}")
            to_close.append((p, tag))
        else:
            kept += 1
            if decision is not None:
                rsn = ", ".join(decision.get("reasons", [])) or f"score={decision['score']}"
                print(f"[rescue-keep] #{p.ticket} {p.symbol} {p.comment} "
                      f"consumed={consumed_pct:.0f}% score={decision['score']} — {rsn}")
    if not to_close:
        return 0, 0.0, kept
    t0 = time.perf_counter()
    # Track which combos we're closing so we can write cooldown after success
    combos_being_closed = [(p.symbol, position_strategy(p) or "unknown") for p, _ in to_close]
    futures = [_CLOSE_POOL.submit(_send_close_request, p, f"{reason}-{tag}")
               for p, tag in to_close]
    closed = 0
    realized = 0.0
    for fut in as_completed(futures):
        ok, pnl = fut.result()
        if ok:
            closed += 1
            realized += pnl
    dt_ms = (time.perf_counter() - t0) * 1000
    print(f">>> SMART BUCKET CLOSE: {closed}/{len(to_close)} positions in {dt_ms:.1f}ms  "
          f"({kept} mild-loser positions kept open)")
    # Write cooldown so the bot doesn't immediately re-fire the same combos
    if closed > 0:
        _write_bot_cooldown(combos_being_closed)
    return closed, realized, kept


def detect_position_closures():
    """Compare current open positions vs last-known list. For each closure,
    pull the realized P&L from history. Updates:
      • strategy_health tracker (so decaying strategies get auto-deactivated)
      • day_pnl (so daily loss circuit breaker fires correctly from SL/TP/agent closes,
        not just bot-initiated bucket closures)"""
    current_positions = my_positions()
    current_ticket_map = {p.ticket: (p.symbol, position_strategy(p))
                          for p in current_positions}
    closed_tickets = set(state["tracked_tickets"].keys()) - set(current_ticket_map.keys())
    if closed_tickets:
        # Fetch recent history to find their realized P&L
        from_ts = int(datetime.now(timezone.utc).timestamp() - 86400)
        deals = mt5.history_deals_get(from_ts, datetime.now().timestamp()) or []
        today_start_ts = int(datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp())
        for ticket in closed_tickets:
            sym, strat = state["tracked_tickets"].get(ticket, (None, None))
            if sym is None or strat is None: continue
            realized = 0.0
            found = False
            close_time = 0
            for d in deals:
                if d.position_id == ticket:
                    realized += d.profit + d.swap + getattr(d, "commission", 0.0)
                    found = True
                    if d.entry == mt5.DEAL_ENTRY_OUT and d.time > close_time:
                        close_time = d.time
            if found:
                health.record_closed_trade(state["health"], sym, strat, realized)
                # If this ticket had crossed 70%-SL during its life, record
                # whether it ultimately recovered (closed profitable) so the
                # rescue layer learns per-(sym, strat) recovery rate live.
                try:
                    rescue.resolve_closed_ticket(ticket, realized,
                                                 state["mfe_mae"], state["health"])
                except Exception as e:
                    print(f"[rescue-resolve err] #{ticket}: {e}")
                # Add to day_pnl ONLY if:
                #   (a) the OUT deal happened today UTC (not from yesterday's tail), AND
                #   (b) this ticket wasn't already counted (e.g., by bot's own bucket close)
                already_counted = ticket in state["day_pnl_counted_tickets"]
                if close_time >= today_start_ts and not already_counted:
                    state["day_pnl"] += realized
                    state["day_pnl_counted_tickets"].add(ticket)
                    print(f"[health] recorded close {sym} {strat} pnl=${realized:+.2f}  "
                          f"day_pnl=${state['day_pnl']:+.2f}")
                else:
                    print(f"[health] recorded close {sym} {strat} pnl=${realized:+.2f}  "
                          f"(day_pnl untouched: {'already counted' if already_counted else 'pre-today close'})")
        health.save_health(state["health"])
    state["tracked_tickets"] = current_ticket_map


def on_tick():
    if datetime.now() > EXP_DATE:
        log_once("exp", "[expired]"); return
    acct = mt5.account_info()
    if acct is None: return
    if acct.login != ACCT_NO:
        log_once("acct", f"[account] {acct.login} != configured {ACCT_NO}")
        return

    reset_day_if_needed()
    # Shared peak-equity store (persisted, read by both bot and agent so
    # drawdown calculations stay consistent across restarts).
    state["peak_equity"] = peak_store.update_peak(acct.equity, source="bot")

    # Detect any positions that closed since last tick → feed health tracker
    detect_position_closures()

    # ===== MFE/MAE TRACKING + 70%-touch detection =====
    # Refresh per-position high/low watermarks, then mark any first crossings
    # of the 70%-SL threshold so the rescue layer can learn live recovery rates.
    try:
        positions_now = my_positions()
        live_tickets = {p.ticket for p in positions_now}
        # Drop tracking for closed tickets
        for stale in [t for t in state["mfe_mae"] if t not in live_tickets]:
            state["mfe_mae"].pop(stale, None)
        for p in positions_now:
            tick = mt5.symbol_info_tick(p.symbol)
            if tick is None:
                continue
            cur_price = tick.bid if p.type == mt5.POSITION_TYPE_BUY else tick.ask
            rescue.update_mfe_mae(p, cur_price, state["mfe_mae"])
            consumed_pct, _ = _consumed_pct_for_position(p)
            if consumed_pct is not None:
                rescue.mark_70pct_touch_if_new(p, consumed_pct,
                                               state["mfe_mae"], state["health"])
        # Periodic persist
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if now_ts - state.get("last_mfe_save_ts", 0) >= MFE_SAVE_INTERVAL_SEC:
            save_mfe_mae()
            state["last_mfe_save_ts"] = now_ts
    except Exception as e:
        print(f"[mfe-mae err] {e}")

    # Dynamic per-trade risk: refreshed every tick from current equity,
    # then scaled by the agent's risk multiplier (1.0 normal, 0.5 in drawdown).
    risk_mult = read_risk_multiplier()
    if USE_DYNAMIC_RISK:
        dynamic_risk = max(0.01, acct.equity * RISK_PER_TRADE_PCT / 100.0) * risk_mult
        set_current_risk_usd(dynamic_risk)
        daily_loss_cap = acct.equity * MAX_DAILY_LOSS_PCT / 100.0
    else:
        set_current_risk_usd(RISK_PER_TRADE_USD * risk_mult)
        daily_loss_cap = MAX_DAILY_LOSS_USD

    if risk_mult < 1.0:
        log_once("risk_mult", f"[agent-risk] using {risk_mult:.2f}× risk (drawdown protection)")

    # News blackout — agent has flagged a news window; skip NEW entries (existing trades unaffected)
    if is_news_blackout():
        log_once("news_blackout", "[agent-news] in news blackout window — skipping new entries")
        return

    # ===== TRADE INTELLIGENCE: POST-ENTRY SL MIGRATION =====
    # Move SL to BE+small when trade is 50% to TP. Age-decay TP for old profitable trades.
    if USE_SL_MIGRATION:
        try:
            n_migrated = ti.migrate_position_stops(my_positions())
            if n_migrated > 0:
                print(f"[sl-migration] {n_migrated} position(s) protected this tick")
        except Exception as e:
            print(f"[sl-migration err] {e}")

    # Bucket-TP check: dynamic target scales with open positions.
    # SMART TRIGGER: when smart close is enabled, the trigger uses
    # CLOSEABLE P&L (winners + near-SL losers), not total floating. This means
    # a deep mild-loser like NZDJPY -$300 doesn't prevent locking in $200 of
    # winners. The smart close then ACTUALLY closes only those closeable
    # positions, leaving the mild loser alone to play out to its natural TP/SL.
    if USE_BUCKET_TP:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if now_ts - state["last_bucket_fire_ts"] >= BUCKET_COOLDOWN_SEC:
            open_pos_count = len(my_positions())
            bucket_target = compute_bucket_target(open_pos_count, acct.equity)
            total_fp = floating_pnl()
            if USE_SMART_BUCKET_CLOSE:
                close_pnl, n_closeable, n_kept = closeable_floating_pnl()
                trigger_pnl = close_pnl
                trigger_label = f"closeable=${close_pnl:.2f} ({n_closeable} positions, {n_kept} kept)"
            else:
                trigger_pnl = total_fp
                trigger_label = f"floating=${total_fp:.2f}"
            if trigger_pnl >= bucket_target:
                # Snapshot tickets before close so we can mark them counted in day_pnl
                # (prevents double-count when detect_position_closures sees them gone next tick)
                pre_close_tickets = {p.ticket for p in my_positions()}
                if USE_SMART_BUCKET_CLOSE:
                    n, realized, kept = smart_bucket_close("bucket-smart")
                    # No fallback to close-all: if smart found nothing to close,
                    # we genuinely have only mild losers — leave them alone.
                else:
                    n, realized = close_all_bot_positions("bucket-tp")
                state["day_pnl"] += realized
                # Mark closed tickets as counted so the next detect_position_closures
                # tick doesn't add them again from history
                post_close_tickets = {p.ticket for p in my_positions()}
                closed_now = pre_close_tickets - post_close_tickets
                state["day_pnl_counted_tickets"].update(closed_now)
                state["last_bucket_fire_ts"] = now_ts
                print(f">>> BUCKET TP FIRED: {trigger_label} >= ${bucket_target:.2f}  "
                      f"(mode={BUCKET_TP_MODE}, total_fp=${total_fp:.2f})  "
                      f"closed {n} positions, realized=${realized:+.2f}  "
                      f"day P&L=${state['day_pnl']:+.2f}")
                # Reset bar-close tracking so the next tick re-scans every (symbol, tf)
                # against the most recent bar. Without this, the bot would wait for the
                # NEXT bar close (up to 1 hour for H1, 24h for D1) before firing again.
                state["last_bar_close"] = {}
                print(">>> bucket reset: bar tracking cleared — next tick will re-evaluate all signals")
                return   # let positions settle one tick before scanning for new signals

    # Portfolio circuit breakers
    dd_pct = 100 * (state["peak_equity"] - acct.equity) / max(state["peak_equity"], 1)
    if dd_pct > MAX_TOTAL_DD_PCT:
        log_once("dd_halt", f"[HALT] equity drawdown {dd_pct:.1f}% > {MAX_TOTAL_DD_PCT}%  — no new trades")
        return
    if state["day_pnl"] <= -abs(daily_loss_cap):
        log_once("day_halt", f"[HALT] daily loss ${state['day_pnl']:.2f} <= -${daily_loss_cap:.2f}  — no new trades today")
        return
    open_pos = my_positions()
    if len(open_pos) >= MAX_OPEN_POSITIONS:
        log_once("max_pos", f"[wait] {len(open_pos)} positions open >= max {MAX_OPEN_POSITIONS}")
        return

    # Group combos by (symbol, timeframe) so each TF is scanned independently
    sym_tf_combos = {}   # (sym, tf_const) -> [strategy_names]
    for sym, strat in ACTIVE_COMBINATIONS:
        tf = strategy_timeframe(strat)
        sym_tf_combos.setdefault((sym, tf), []).append(strat)

    for (sym, tf), strats in sym_tf_combos.items():
        cache_symbol_info(sym)
        if sym not in state["sym_info"]: continue
        bars = closed_bars(sym, tf, 210)
        if bars is None: continue
        latest_close = int(bars[-1]['time'])
        prev = state["last_bar_close"].get((sym, tf), 0)
        if latest_close == prev: continue
        # NEW bar closed for this (symbol, timeframe)
        state["last_bar_close"][(sym, tf)] = latest_close
        tf_name = "D1" if tf == mt5.TIMEFRAME_D1 else "H1" if tf == mt5.TIMEFRAME_H1 else f"TF{tf}"
        if VERBOSE:
            print(f"[{tf_name}-close] {sym} @ {datetime.fromtimestamp(latest_close, tz=timezone.utc)}")

        # Off-market cooldown — skip entire symbol if broker recently said "trade disabled"
        cooldown_until = state["market_closed_until"].get(sym, 0)
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if now_ts < cooldown_until:
            log_once(f"offmkt_skip_{sym}",
                     f"[off-market] {sym} cooldown until "
                     f"{datetime.fromtimestamp(cooldown_until, tz=timezone.utc).strftime('%H:%M UTC')} — skipping all strategies")
            continue

        info = state["sym_info"][sym]
        for strat in strats:
            # Skip if we already have a position for this (sym, strat).
            # Identification is by COMMENT (stable across config changes) with
            # magic as a secondary check.
            magic = magic_for(sym, strat)
            existing = [p for p in my_positions(sym)
                        if position_strategy(p) == strat or p.magic == magic]
            if existing: continue

            # Strategy health check: skip if this combination has been auto-deactivated
            if not health.is_active(state["health"], sym, strat):
                log_once(f"health_skip_{sym}_{strat}",
                         f"[health] {sym} {strat} is deactivated due to recent poor performance — skipping")
                continue

            # Per-symbol cap: don't stack too many strategies on one volatile pair.
            # If we already hold MAX_POSITIONS_PER_SYMBOL on this symbol, skip.
            current_on_sym = len(my_positions(sym))
            if current_on_sym >= MAX_POSITIONS_PER_SYMBOL:
                log_once(f"sym_cap_{sym}",
                         f"[sym-cap] {sym} already has {current_on_sym} positions "
                         f"(cap={MAX_POSITIONS_PER_SYMBOL}) — skipping {strat}")
                continue

            # Agent-imposed cooldown: monitor agent recently force-closed this
            # (sym, strat) combo (correlation guard / stale cleanup). Wait for
            # the cooldown to expire before reopening — prevents churn.
            if is_combo_in_cooldown(sym, strat):
                log_once(f"cooldown_{sym}_{strat}",
                         f"[cooldown] {sym} {strat} blocked by agent — skipping")
                continue

            # Per-symbol risk override: scale risk for this specific symbol
            # (e.g. XAUUSD 0.6× = use 60% of default risk because gold is volatile)
            sym_risk_mult = RISK_MULTIPLIER_BY_SYMBOL.get(sym, 1.0)
            if sym_risk_mult != 1.0:
                # Temporarily lower the global risk for this detector call
                base_risk = get_current_risk_usd()
                set_current_risk_usd(base_risk * sym_risk_mult)
            detector = STRATEGY_DETECTORS.get(strat)
            if detector is None:
                if sym_risk_mult != 1.0:
                    set_current_risk_usd(base_risk)   # restore
                continue
            try:
                plan = detector(bars, sym, info["usd_per_pp"], info["spread"])
            except Exception as e:
                print(f"[detect err] {sym} {strat}: {e}")
                if sym_risk_mult != 1.0:
                    set_current_risk_usd(base_risk)   # restore
                continue
            # Restore original risk for next iteration regardless of plan/no-plan
            if sym_risk_mult != 1.0:
                set_current_risk_usd(base_risk)
            if plan is None: continue

            # ===== TRADE INTELLIGENCE: CAP TP DISTANCE =====
            # Pull in TPs that are too far from entry (e.g. rsi2 on XAUUSD
            # naturally wants 5%+ moves; cap to a sensible per-asset-class max)
            plan = ti.cap_tp_distance(plan)

            # ===== TRADE INTELLIGENCE: PRE-TRADE QUALITY GATE =====
            if USE_QUALITY_FILTER:
                try:
                    score, breakdown = ti.compute_quality_score(plan, bars, info, state)
                except Exception as e:
                    print(f"[quality-filter err] {sym} {strat}: {e}")
                    score, breakdown = 100.0, {}   # fail-open: don't block on scorer error
                if score < QUALITY_THRESHOLD:
                    log_once(f"qfilter_{sym}_{strat}",
                             f"[quality-filter] {sym} {strat} score={score:.0f} < {QUALITY_THRESHOLD} — skipped")
                    if QUALITY_VERBOSE and breakdown:
                        bd = "  ".join(f"{k}={v:.0f}" for k, v in breakdown.items())
                        print(f"  breakdown: {bd}")
                    continue
                if QUALITY_VERBOSE:
                    bd = "  ".join(f"{k}={v:.0f}" for k, v in breakdown.items())
                    print(f"[quality-filter] {sym} {strat} score={score:.0f} ≥ {QUALITY_THRESHOLD} OK ({bd})")

            # FIRE
            # Daily trade cap — halt new entries after N opens today (signals queue to tomorrow)
            if MAX_TRADES_OPENED_PER_DAY > 0 and state["day_trades_opened"] >= MAX_TRADES_OPENED_PER_DAY:
                log_once("day_trade_cap",
                         f"[day-cap] {state['day_trades_opened']} trades opened today >= {MAX_TRADES_OPENED_PER_DAY} cap — pausing new entries")
                break
            if not send_market_with_sltp(plan):
                continue
            state["day_trades_opened"] += 1
            # Re-check position cap
            if len(my_positions()) >= MAX_OPEN_POSITIONS:
                break


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
def recover_state():
    pos = my_positions()
    if not pos:
        print("[recovery] no open positions"); return
    print(f"[recovery] adopting {len(pos)} existing positions:")
    for p in pos:
        strat = position_strategy(p) or "unknown"
        print(f"  #{p.ticket}  {p.symbol:<7} {strat:<13} "
              f"{'BUY' if p.type==mt5.POSITION_TYPE_BUY else 'SELL'}  "
              f"{p.volume}@{p.price_open:.5f}  SL={p.sl:.5f}  TP={p.tp:.5f}")

def main():
    print(">>> d1_portfolio_bot starting...", flush=True)

    # Single-instance lock: refuses to start if another bot is already running.
    # Prevents two bots opening duplicate trades with the same magic numbers.
    from process_lock import acquire_or_die
    acquire_or_die("d1_portfolio_bot")

    if not mt5.initialize():
        print("MT5 init failed:", mt5.last_error()); return

    # Validate account
    acct = mt5.account_info()
    if acct is None:
        print("No account info"); mt5.shutdown(); return
    if acct.login != ACCT_NO:
        print(f"WARNING: logged into {acct.login} but config says {ACCT_NO} — bot will idle")
    print(f"Account: {acct.login}  Equity: ${acct.equity:.2f}  Currency: {acct.currency}")
    # Initialize peak from persisted store (preserves history across restarts)
    state["peak_equity"] = peak_store.update_peak(acct.equity, source="bot")
    print(f"[peak] persisted peak_equity=${state['peak_equity']:.2f}")
    state["day"] = today_utc()
    state["health"] = health.load_health()
    n_inactive = sum(1 for v in state["health"].values() if v.get("status") == "inactive")
    if n_inactive:
        print(f"[health] loaded — {n_inactive} combinations currently deactivated")

    # MFE/MAE state recovery (so 70%-touch markers and per-ticket watermarks
    # survive restart). Prunes any tickets no longer open.
    state["mfe_mae"] = load_mfe_mae()
    if state["mfe_mae"]:
        live_tickets = {p.ticket for p in (mt5.positions_get() or [])}
        before = len(state["mfe_mae"])
        state["mfe_mae"] = {t: v for t, v in state["mfe_mae"].items() if t in live_tickets}
        print(f"[rescue] mfe_mae loaded — {len(state['mfe_mae'])} active tickets "
              f"(pruned {before - len(state['mfe_mae'])} stale)")
    if USE_LOSER_RESCUE:
        from d1_portfolio_config import RESCUE_KEEP_THRESHOLD, RESCUE_HARD_CLOSE_PCT
        print(f"[rescue] loser-rescue layer ENABLED  keep>={RESCUE_KEEP_THRESHOLD}  "
              f"hard-close>={RESCUE_HARD_CLOSE_PCT}% (see d1_portfolio_config to tune)")

    # Subscribe to symbols and cache info
    for sym in {s for s, _ in ACTIVE_COMBINATIONS}:
        cache_symbol_info(sym)
        info = state["sym_info"].get(sym)
        if info:
            print(f"  {sym:<7} ready  spread={info['spread']:.4f}  $/lot/$1={info['usd_per_pp']:.4f}")
        else:
            print(f"  {sym:<7} UNAVAILABLE — will be skipped")

    state["filling_mode"] = resolve_filling_mode("EURUSD")   # generic
    if USE_DYNAMIC_RISK:
        live_risk = max(0.01, acct.equity * RISK_PER_TRADE_PCT / 100.0)
        live_loss_cap = acct.equity * MAX_DAILY_LOSS_PCT / 100.0
        risk_str = (f"DYNAMIC {RISK_PER_TRADE_PCT}% of equity = "
                    f"${live_risk:.2f}/trade  daily_loss_cap=${live_loss_cap:.2f}")
    else:
        risk_str = f"FIXED ${RISK_PER_TRADE_USD}/trade"
    print(f"\nActive combinations: {len(ACTIVE_COMBINATIONS)}  risk_per_trade={risk_str}  "
          f"max_open_positions={MAX_OPEN_POSITIONS}")
    recover_state()

    # ----- Pre-populate last_bar_close so we DON'T fire on stale bars on restart -----
    # Without this, the bot's first tick after restart treats every "most recent
    # bar that closed before we started" as a new signal — entering against current
    # price using stale signal logic. With this, we only fire when a TRULY new bar
    # closes AFTER the bot started.
    print("[startup] priming bar-close tracker to skip stale bars...")
    primed = 0
    for sym, strat in ACTIVE_COMBINATIONS:
        tf = strategy_timeframe(strat)
        if sym not in state["sym_info"]: continue
        bars = closed_bars(sym, tf, 2)
        if bars is None: continue
        latest_close = int(bars[-1]['time'])
        if state["last_bar_close"].get((sym, tf)) != latest_close:
            state["last_bar_close"][(sym, tf)] = latest_close
            primed += 1
    print(f"[startup] primed {primed} (symbol, timeframe) keys — bot will fire ONLY on bars closing AFTER this moment")

    print("\n>>> running.  Polling every 60s; trades fire when D1 bars close.\n", flush=True)
    try:
        while True:
            on_tick()
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main()
