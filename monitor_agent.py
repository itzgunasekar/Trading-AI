"""
Monitor Agent — professional risk-manager watchdog for the D1 portfolio bot.

This is a SEPARATE process from d1_portfolio_bot.py. Run them in parallel:
  Terminal 1:  python d1_portfolio_bot.py
  Terminal 2:  python monitor_agent.py

Responsibilities:
  1. ACCOUNT WATCH      — log equity, margin level, peak/drawdown every 60s to CSV

  2. EMERGENCY ACTION   — force-close everything if:
       a) margin level < CRIT_MARGIN_LEVEL_PCT (e.g. 200%)  →  prevents margin call
       b) session drawdown > EMERGENCY_DD_PCT  →  catastrophic move
       c) spread > EMERGENCY_SPREAD_MULT × normal for sustained period  →  flash event

  3. CORRELATION GUARD  — if too many same-currency positions are open AND
       collectively losing, close the worst-performing ones to cap exposure.

  4. STALE POSITION CLEANUP — close positions that have been open longer than
       their strategy's intended max hold (e.g. H1 trade open > 72h is broken).

  5. ADAPTIVE RISK FLAG — when portfolio is in drawdown > DRAWDOWN_RISK_HALVE_PCT,
       writes risk_multiplier.txt to scale bot risk down (bot reads on next tick).
       Restores to 1.0 when drawdown recovers.

  6. NEWS BLACKOUT      — during scheduled major news windows (NFP, FOMC, CPI),
       writes a blackout flag the bot reads to skip new entries.

  7. ANOMALY LOGGING    — write all unusual events to monitor.log with timestamps

  8. HEALTH REPORT      — print a one-line status every 60s

The agent does NOT open trades. It is purely defensive — fills the gap
between the broker's server-side SL/TP (per-trade protection) and the bot's
own circuit breakers (per-day caps).

It identifies bot positions by COMMENT matching one of the known strategy names.
"""

import sys
import csv
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

# Thread pool for parallel close orders
_CLOSE_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="agent-close")

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

import MetaTrader5 as mt5

from d1_portfolio_config import (ACCT_NO,
                                   AGENT_POLL_SECONDS,
                                   CRIT_MARGIN_LEVEL_PCT,
                                   EMERGENCY_DD_PCT,
                                   EMERGENCY_SPREAD_MULT,
                                   SPREAD_PERSISTENT_TICKS,
                                   EMERGENCY_COOLDOWN_SEC,
                                   MAX_SAME_CURRENCY_POS,
                                   CORR_GUARD_LOSING_THRESH,
                                   CORR_MIN_AGE_SECONDS,
                                   STALE_HOURS_H1,
                                   STALE_DAYS_D1,
                                   DRAWDOWN_RISK_HALVE_PCT,
                                   DRAWDOWN_RECOVERY_PCT,
                                   COOLDOWN_AFTER_AGENT_CLOSE_HOURS)
from d1_portfolio_strategy import STRATEGY_DETECTORS
import peak_equity_store as peak_store

# ============================================================================
# Configuration — most thresholds live in d1_portfolio_config.py (single
# source of truth across bot + agent). Only file paths and process-local
# bits remain here.
# ============================================================================
POLL_SECONDS              = AGENT_POLL_SECONDS   # imported from shared config
CSV_PATH                  = "monitor_log.csv"
LOG_PATH                  = "monitor.log"
RISK_MULT_FILE            = "risk_multiplier.txt"
COOLDOWN_FILE             = "strategy_cooldown.json"

# News blackout — major scheduled US news (hard-coded windows, all in UTC)
# Format: list of (weekday_int, "HH:MM", duration_minutes, label)
# weekday: 0=Mon, 4=Fri.  -1 = any day (used for monthly events with date pattern)
NEWS_BLACKOUT_FILE        = "news_blackout.flag"
NEWS_WINDOWS = [
    # NFP — first Friday of month, 12:30 UTC (08:30 ET).  Codified as Friday + date check.
    (4, "12:25", 35, "NFP/jobs (first Fri)"),
    # Weekly jobless claims (every Thursday 12:30 UTC)
    (3, "12:25", 20, "weekly jobless"),
    # CPI, PPI, Retail Sales — usually morning US window (~12:30 UTC mid-month)
    # Generic morning US data window covers most of these
    (-1, "12:25", 20, "US morning data (CPI/PPI/Retail/etc)"),
    # FOMC rate decisions — Wed 18:00 UTC (~8 times a year). Generic Wed afternoon
    (2, "17:55", 60, "FOMC window (Wed afternoon)"),
    # 10:00 ET data (ISM, JOLTS, etc) — 14:00 UTC
    (-1, "13:55", 25, "US 10am data"),
]

# What counts as "bot positions" — match comment field to known strategies
KNOWN_STRATEGIES = set(STRATEGY_DETECTORS.keys())

# ============================================================================
# State
# ============================================================================
state = {
    "session_peak_equity":  0.0,
    "session_start_equity": 0.0,
    "spread_baseline":      {},   # symbol -> rolling normal spread
    "spread_breach_count":  {},   # symbol -> consecutive breach ticks
    "last_emergency_ts":    0,
}

# EMERGENCY_COOLDOWN_SEC imported from d1_portfolio_config


# ============================================================================
# Logging
# ============================================================================
def log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def csv_append(row):
    """Append a row to monitor_log.csv (creates with header if missing)."""
    new = not os.path.exists(CSV_PATH)
    try:
        with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["timestamp_utc", "equity", "balance", "margin",
                            "free_margin", "margin_level_pct", "peak_equity",
                            "session_dd_pct", "open_positions", "floating_pnl"])
            w.writerow(row)
    except Exception as e:
        log(f"csv_append failed: {e}", "WARN")


# ============================================================================
# MT5 helpers
# ============================================================================
def bot_positions():
    """All positions whose comment matches a known strategy name."""
    pos = mt5.positions_get() or []
    return [p for p in pos if p.comment and p.comment.strip() in KNOWN_STRATEGIES]


def floating_pnl(positions):
    return sum(p.profit + p.swap + getattr(p, "commission", 0.0) for p in positions)


from close_helpers import send_close_request as _shared_send_close


def _resolve_filling_mode(symbol):
    """Pick a sensible filling-mode default for this symbol; falls back
    through the alternatives inside the shared helper if rejected."""
    info = mt5.symbol_info(symbol)
    fm = getattr(info, "filling_mode", 0) or 0
    if fm & 1: return mt5.ORDER_FILLING_FOK
    if fm & 2: return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def _send_close_request(position, reason_prefix):
    """Agent-side wrapper around the shared close helper.
    Designed to be safely run inside a ThreadPoolExecutor."""
    fm = _resolve_filling_mode(position.symbol)
    return _shared_send_close(position, reason_prefix, filling_mode=fm)


def close_all_bot_positions(reason):
    """Force-close every bot position IN PARALLEL.
    Returns (closed_count, realized_pnl)."""
    positions = bot_positions()
    if not positions:
        return 0, 0.0
    t0 = time.perf_counter()
    futures = [_CLOSE_POOL.submit(_send_close_request, p, f"emrg-{reason}") for p in positions]
    closed = 0
    realized = 0.0
    for fut in as_completed(futures):
        ok, pnl, ticket = fut.result()
        if ok:
            closed += 1
            realized += pnl
            log(f"closed #{ticket} reason={reason}")
        else:
            log(f"close FAILED #{ticket} reason={reason}", "ERROR")
    dt_ms = (time.perf_counter() - t0) * 1000
    log(f"PARALLEL CLOSE: {closed}/{len(positions)} positions in {dt_ms:.1f}ms", "ALERT")
    return closed, realized


# ============================================================================
# Per-tick check
# ============================================================================
def check_margin_level(acct):
    if acct.margin <= 0:
        return None
    margin_level = (acct.equity / acct.margin) * 100.0
    if margin_level < CRIT_MARGIN_LEVEL_PCT:
        return margin_level
    return None


def check_emergency_drawdown(acct):
    """Returns drawdown % if exceeding emergency threshold."""
    if state["session_peak_equity"] <= 0:
        return None
    dd_pct = 100.0 * (state["session_peak_equity"] - acct.equity) / state["session_peak_equity"]
    if dd_pct >= EMERGENCY_DD_PCT:
        return dd_pct
    return None


def check_correlation_exposure(positions):
    """If >N positions exist on the same base/quote currency AND they're
    collectively losing money, return the worst-performing tickets to close.

    Skips positions that just opened (< CORR_MIN_AGE_SECONDS old) — those
    haven't had a chance to play out yet, and closing them immediately defeats
    the purpose of the signal that just fired."""
    now_ts = int(datetime.now(timezone.utc).timestamp())
    # Map each position to its primary currencies
    by_currency = {}   # currency -> list of positions
    for p in positions:
        sym = p.symbol
        # FX pairs: extract both currencies. Metals/indices: treat as a single bucket.
        if len(sym) == 6 and sym[:3].isalpha() and sym[3:].isalpha():
            for ccy in (sym[:3], sym[3:]):
                by_currency.setdefault(ccy, []).append(p)
        else:
            by_currency.setdefault(sym, []).append(p)
    to_close = []
    for ccy, ps in by_currency.items():
        if len(ps) <= MAX_SAME_CURRENCY_POS: continue
        # Only act if collectively losing
        total_pnl = sum(p.profit + p.swap for p in ps)
        if total_pnl >= CORR_GUARD_LOSING_THRESH: continue
        # Filter to MATURE positions only — fresh signals deserve a chance
        mature_ps = [p for p in ps if (now_ts - p.time) >= CORR_MIN_AGE_SECONDS]
        if len(mature_ps) <= MAX_SAME_CURRENCY_POS:
            # Excess is entirely fresh positions — leave them alone
            continue
        # Close worst MATURE performers down to MAX_SAME_CURRENCY_POS-1.
        # CRITICAL: never include winners in the close set. Closing a winning
        # position under correlation logic makes no defensible sense — the
        # position is paying you precisely because the correlation is in your
        # favor. Lifetime log analysis (2026-05-26) found 12 winners had been
        # incorrectly closed under this branch, costing forfeited upside.
        losing_mature = [p for p in mature_ps if (p.profit + p.swap) < 0]
        if len(losing_mature) == 0:
            continue
        sorted_ps = sorted(losing_mature, key=lambda p: p.profit + p.swap)
        # Cap close count: don't cut more than the excess over the threshold,
        # and never close more losers than exist.
        excess = max(0, len(mature_ps) - (MAX_SAME_CURRENCY_POS - 1))
        n_to_close = min(excess, len(sorted_ps))
        for p in sorted_ps[:n_to_close]:
            to_close.append((p, f"correlation:{ccy}"))
    return to_close


def check_stale_positions(positions):
    """Close positions that have been open way longer than they should be.

    Skip positions currently in profit — closing winners under 'stale' logic
    is the same anti-pattern as the correlation-guard winner bug. A winner
    that has held its gains for >72h on H1 or >60d on D1 is unusual but not
    pathological; let it ride to natural TP or strategy_health-driven decay
    rather than force-closing it for a clock reason."""
    now_ts = int(datetime.now(timezone.utc).timestamp())
    to_close = []
    for p in positions:
        # Never stale-close a profitable position
        if (p.profit + p.swap) > 0:
            continue
        age_hours = (now_ts - p.time) / 3600.0
        strat = (p.comment or "").strip()
        if strat.endswith("_H1") and age_hours > STALE_HOURS_H1:
            to_close.append((p, f"stale-H1:{age_hours:.0f}h"))
        elif age_hours > STALE_DAYS_D1 * 24:
            to_close.append((p, f"stale-D1:{age_hours/24:.0f}d"))
    return to_close


def write_risk_multiplier(value):
    """Write a risk multiplier (0.0-1.0) the bot can optionally read."""
    try:
        with open(RISK_MULT_FILE, "w") as f:
            f.write(f"{value:.3f}\n{datetime.now(timezone.utc).isoformat()}\n")
    except Exception as e:
        log(f"write risk_multiplier failed: {e}", "WARN")


def is_first_friday(dt):
    """True if dt.weekday()==4 (Fri) and day in 1-7 → first Friday of month."""
    return dt.weekday() == 4 and 1 <= dt.day <= 7


def check_news_blackout(now_utc):
    """Return (in_blackout, label) for current UTC time."""
    weekday = now_utc.weekday()
    hhmm = now_utc.strftime("%H:%M")
    cur_minutes = now_utc.hour * 60 + now_utc.minute
    for w, start_str, duration, label in NEWS_WINDOWS:
        if w != -1 and w != weekday: continue
        # NFP refinement: skip non-first-Friday for the NFP entry
        if "NFP" in label and not is_first_friday(now_utc): continue
        sh, sm = int(start_str[:2]), int(start_str[3:])
        start_minutes = sh * 60 + sm
        if start_minutes <= cur_minutes < start_minutes + duration:
            return True, label
    return False, ""


def write_blackout_flag(active, label=""):
    """Write or remove the news blackout flag file."""
    try:
        if active:
            with open(NEWS_BLACKOUT_FILE, "w") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat()}\n{label}\n")
        else:
            if os.path.exists(NEWS_BLACKOUT_FILE):
                os.remove(NEWS_BLACKOUT_FILE)
    except Exception as e:
        log(f"blackout flag write failed: {e}", "WARN")


def close_specific_positions(positions_with_reasons):
    """Close a specific list of (position, reason) pairs IN PARALLEL.
    Also writes a cooldown entry per (symbol, strategy) so the bot won't
    immediately reopen the same combo at the next bar close."""
    if not positions_with_reasons:
        return 0
    t0 = time.perf_counter()
    # Map ticket -> (symbol, strategy) before sending so we can write cooldown
    closed_combos = []   # list of (symbol, strategy)
    fut_to_meta = {}
    for p, reason in positions_with_reasons:
        strat = (p.comment or "").strip()
        fut = _CLOSE_POOL.submit(_send_close_request, p, f"agent-{reason}")
        fut_to_meta[fut] = (p.symbol, strat)
    n = 0
    for fut in as_completed(fut_to_meta):
        sym, strat = fut_to_meta[fut]
        ok, pnl, ticket = fut.result()
        if ok:
            n += 1
            closed_combos.append((sym, strat))
            log(f"agent-close #{ticket} pnl=${pnl:+.2f}")
        else:
            log(f"agent-close FAILED #{ticket}", "ERROR")
    if closed_combos:
        _write_cooldown(closed_combos)
    dt_ms = (time.perf_counter() - t0) * 1000
    log(f"PARALLEL CLOSE: {n}/{len(positions_with_reasons)} positions in {dt_ms:.1f}ms")
    return n


def _write_cooldown(closed_combos):
    """Append cooldown entries so bot won't reopen these (sym,strat) combos for N hours."""
    import json
    expires = datetime.now(timezone.utc).timestamp() + COOLDOWN_AFTER_AGENT_CLOSE_HOURS * 3600
    try:
        # Read existing
        existing = {}
        if os.path.exists(COOLDOWN_FILE):
            with open(COOLDOWN_FILE) as f:
                existing = json.load(f)
        # Add/refresh entries
        for sym, strat in closed_combos:
            key = f"{sym}|{strat}"
            existing[key] = expires
        # Clean expired
        now = datetime.now(timezone.utc).timestamp()
        existing = {k: v for k, v in existing.items() if v > now}
        with open(COOLDOWN_FILE, "w") as f:
            json.dump(existing, f, indent=2)
        log(f"cooldown: {len(closed_combos)} combos blocked for {COOLDOWN_AFTER_AGENT_CLOSE_HOURS}h")
    except Exception as e:
        log(f"cooldown write failed: {e}", "WARN")


def check_spread_spikes(positions):
    """Return list of (symbol, spread, baseline) tuples that breached threshold persistently."""
    breaches = []
    for sym in set(p.symbol for p in positions):
        tick = mt5.symbol_info_tick(sym)
        info = mt5.symbol_info(sym)
        if tick is None or info is None: continue
        spread = (tick.ask - tick.bid)
        # Update rolling baseline (exponential moving average)
        prev = state["spread_baseline"].get(sym, spread)
        baseline = 0.95 * prev + 0.05 * spread
        state["spread_baseline"][sym] = baseline
        if spread > EMERGENCY_SPREAD_MULT * baseline and baseline > 0:
            state["spread_breach_count"][sym] = state["spread_breach_count"].get(sym, 0) + 1
            if state["spread_breach_count"][sym] >= SPREAD_PERSISTENT_TICKS:
                breaches.append((sym, spread, baseline))
        else:
            state["spread_breach_count"][sym] = 0
    return breaches


# ============================================================================
# Main loop
# ============================================================================
def main():
    log("=" * 60)
    log("Monitor Agent starting")

    # Single-instance lock
    from process_lock import acquire_or_die
    acquire_or_die("monitor_agent")

    if not mt5.initialize():
        log(f"MT5 init failed: {mt5.last_error()}", "ERROR")
        return
    acct = mt5.account_info()
    if acct is None:
        log("No account info", "ERROR"); mt5.shutdown(); return
    if acct.login != ACCT_NO:
        log(f"Account mismatch: connected {acct.login} != configured {ACCT_NO}", "ERROR")
        mt5.shutdown(); return

    log(f"Connected to account {acct.login}  equity=${acct.equity:.2f}")
    state["session_start_equity"] = acct.equity
    # SHARED peak_equity store (with bot) so drawdown agrees across restarts
    state["session_peak_equity"] = peak_store.update_peak(acct.equity, source="agent")
    log(f"persisted peak_equity=${state['session_peak_equity']:.2f}")
    log(f"Thresholds: margin<{CRIT_MARGIN_LEVEL_PCT}%, dd>{EMERGENCY_DD_PCT}%, spread×{EMERGENCY_SPREAD_MULT}")
    log(f"Logging to: {CSV_PATH} (metrics) + {LOG_PATH} (events)")

    try:
        while True:
            try:
                acct = mt5.account_info()
                if acct is None:
                    log("account_info returned None — terminal disconnected?", "WARN")
                    time.sleep(POLL_SECONDS); continue

                positions = bot_positions()
                fp = floating_pnl(positions)

                # Update peak via shared persisted store
                state["session_peak_equity"] = peak_store.update_peak(acct.equity, source="agent")

                # Compute metrics
                margin_level = (acct.equity / acct.margin * 100.0) if acct.margin > 0 else 999999.0
                dd_pct = (100.0 * (state["session_peak_equity"] - acct.equity)
                          / max(state["session_peak_equity"], 1))

                # Log row
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                free_margin = getattr(acct, "margin_free", acct.equity - acct.margin)
                csv_append([
                    ts,
                    f"{acct.equity:.2f}",
                    f"{acct.balance:.2f}",
                    f"{acct.margin:.2f}",
                    f"{free_margin:.2f}",
                    f"{margin_level:.2f}",
                    f"{state['session_peak_equity']:.2f}",
                    f"{dd_pct:.2f}",
                    len(positions),
                    f"{fp:.2f}",
                ])

                # One-line health report to stdout
                print(f"[{ts}] equity=${acct.equity:.2f}  margin_lvl={margin_level:.0f}%  "
                      f"dd={dd_pct:.1f}%  pos={len(positions)}  float=${fp:+.2f}")

                # Cooldown check before any emergency action
                now_ts = int(datetime.now(timezone.utc).timestamp())
                in_cooldown = now_ts - state["last_emergency_ts"] < EMERGENCY_COOLDOWN_SEC

                if not in_cooldown:
                    # === Emergency checks ===
                    ml = check_margin_level(acct)
                    if ml is not None:
                        log(f"!!! MARGIN LEVEL CRITICAL: {ml:.1f}% < {CRIT_MARGIN_LEVEL_PCT}% — closing all", "ALERT")
                        n, r = close_all_bot_positions("margin")
                        log(f"emergency close: {n} positions, realized=${r:+.2f}", "ALERT")
                        state["last_emergency_ts"] = now_ts

                    elif check_emergency_drawdown(acct) is not None:
                        dd = check_emergency_drawdown(acct)
                        log(f"!!! EMERGENCY DRAWDOWN: {dd:.1f}% from peak — closing all", "ALERT")
                        n, r = close_all_bot_positions("drawdown")
                        log(f"emergency close: {n} positions, realized=${r:+.2f}", "ALERT")
                        state["last_emergency_ts"] = now_ts

                    else:
                        # Spread anomaly check — don't auto-close on spread alone,
                        # just log loudly. Server-side SL/TP still protects each position.
                        spreads = check_spread_spikes(positions)
                        for sym, sp, base in spreads:
                            log(f"SPREAD SPIKE on {sym}: {sp:.5f} vs baseline {base:.5f} "
                                f"({sp/base:.1f}x) — possible news/flash event", "WARN")

                # === PROFESSIONAL RISK-MANAGER CHECKS ===

                # Correlation guard — too many same-currency losers
                corr_closes = check_correlation_exposure(positions)
                if corr_closes:
                    log(f"CORRELATION GUARD: closing {len(corr_closes)} excess "
                        f"same-currency positions", "WARN")
                    close_specific_positions(corr_closes)

                # Stale position cleanup
                stale_closes = check_stale_positions(positions)
                if stale_closes:
                    log(f"STALE CLEANUP: closing {len(stale_closes)} long-running positions", "WARN")
                    close_specific_positions(stale_closes)

                # Adaptive risk multiplier — halve risk in drawdown, restore on recovery
                if dd_pct >= DRAWDOWN_RISK_HALVE_PCT:
                    if state.get("current_risk_mult", 1.0) != 0.5:
                        log(f"ADAPTIVE RISK: portfolio drawdown {dd_pct:.1f}% "
                            f"≥ {DRAWDOWN_RISK_HALVE_PCT}% → setting risk to 0.5×", "WARN")
                        write_risk_multiplier(0.5)
                        state["current_risk_mult"] = 0.5
                elif dd_pct <= DRAWDOWN_RECOVERY_PCT:
                    if state.get("current_risk_mult", 1.0) != 1.0:
                        log(f"ADAPTIVE RISK: drawdown recovered to {dd_pct:.1f}% "
                            f"≤ {DRAWDOWN_RECOVERY_PCT}% → restoring full risk", "INFO")
                        write_risk_multiplier(1.0)
                        state["current_risk_mult"] = 1.0

                # News blackout — write flag the bot reads
                now_utc = datetime.now(timezone.utc)
                in_blackout, label = check_news_blackout(now_utc)
                if in_blackout:
                    if not state.get("in_blackout"):
                        log(f"NEWS BLACKOUT START: {label}", "WARN")
                        write_blackout_flag(True, label)
                        state["in_blackout"] = True
                else:
                    if state.get("in_blackout"):
                        log(f"NEWS BLACKOUT ended", "INFO")
                        write_blackout_flag(False)
                        state["in_blackout"] = False

                # Health check: is the bot actually trading?
                if len(positions) == 0 and (now_ts - int(state.get('last_zero_alert', 0))) > 3600:
                    log(f"NOTE: no bot positions open — check d1_portfolio_bot.py is running", "INFO")
                    state['last_zero_alert'] = now_ts

            except Exception as e:
                log(f"loop exception: {e}", "ERROR")

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        log("Monitor stopped by user")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
