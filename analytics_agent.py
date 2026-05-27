"""
Analytics Agent — trade lifecycle observatory and learning engine.

THIRD process, runs alongside d1_portfolio_bot.py and monitor_agent.py:
  Terminal 1:  python d1_portfolio_bot.py        # the trader
  Terminal 2:  python monitor_agent.py            # the risk manager
  Terminal 3:  python analytics_agent.py          # the analyst (this file)

Responsibilities (pure observation — NEVER trades):
  1. OPEN-EVENT CAPTURE  — when a new bot position appears, snapshot market state
                            at entry (spread, ATR, RSI, BB position, etc.)
  2. CLOSE-EVENT JOIN    — when a position closes, join open snapshot with exit
                            data and write one fully-enriched row to trades.csv
  3. PER-STRATEGY STATS  — running win rate, expectancy, drawdown per strategy
  4. DAILY ROLLUP        — end-of-UTC-day aggregate of all activity
  5. LIVE-vs-BACKTEST    — compare measured live $/day per strategy to the
                            backtest expectation; flag big discrepancies
  6. REPORT              — print human-readable summary on demand (Ctrl+R signal
                            isn't portable on Windows, so we emit every 30 mins)

Output files (everything under ./analytics/):
  trades.csv             — master trade log (one row per closed trade)
  opens.jsonl            — append-only log of entry snapshots
  by_strategy.csv        — rollup per strategy (win%, $/trade, expectancy, etc.)
  by_day.csv             — daily P&L and trade count
  report.txt             — human-readable narrative summary (overwritten each cycle)
"""

import sys
import os
import csv
import json
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

import MetaTrader5 as mt5

from d1_portfolio_config import ACCT_NO
from d1_portfolio_strategy import STRATEGY_DETECTORS

# ============================================================================
# Configuration
# ============================================================================
POLL_SECONDS         = 30           # how often to scan for new opens/closes
REPORT_EVERY_SECONDS = 1800         # full report every 30 minutes
ANALYTICS_DIR        = "analytics"
TRADES_CSV           = f"{ANALYTICS_DIR}/trades.csv"
OPENS_JSONL          = f"{ANALYTICS_DIR}/opens.jsonl"
BY_STRATEGY_CSV      = f"{ANALYTICS_DIR}/by_strategy.csv"
BY_DAY_CSV           = f"{ANALYTICS_DIR}/by_day.csv"
REPORT_TXT           = f"{ANALYTICS_DIR}/report.txt"

KNOWN_STRATS = set(STRATEGY_DETECTORS.keys())

# Expected live performance per strategy (from backtest_v9/v10/v12 results).
# Used to flag strategies that are wildly off from their measured edge.
# $/trade values are at $50 risk per trade — scaled to live risk dynamically.
BACKTEST_EXPECTATION = {
    "donchian20":    {"wr": 35, "avg_pnl_per_50": 2.4},
    "momentum60":    {"wr": 47, "avg_pnl_per_50": 1.6},
    "rsi2":          {"wr": 60, "avg_pnl_per_50": 1.0},
    "3day_reverse":  {"wr": 61, "avg_pnl_per_50": 0.7},
    "bb_extreme":    {"wr": 42, "avg_pnl_per_50": 1.5},
    "donchian20_T":  {"wr": 47, "avg_pnl_per_50": 1.4},
    "momentum60_T":  {"wr": 55, "avg_pnl_per_50": 1.2},
    "donchian20_H1": {"wr": 32, "avg_pnl_per_50": 0.7},
    "momentum60_H1": {"wr": 46, "avg_pnl_per_50": 1.5},
    "rsi2_H1":       {"wr": 58, "avg_pnl_per_50": 0.5},
    "bb_extreme_H1": {"wr": 43, "avg_pnl_per_50": 0.8},
    "consensus":     {"wr": 60, "avg_pnl_per_50": 1.5},
}


# ============================================================================
# State
# ============================================================================
state = {
    "tracked_opens":     {},     # ticket -> open snapshot dict
    "last_report_ts":    0,
    "session_start":     datetime.now(timezone.utc).isoformat(),
}


# ============================================================================
# Setup directories and CSV headers
# ============================================================================
def ensure_dir():
    if not os.path.exists(ANALYTICS_DIR):
        os.makedirs(ANALYTICS_DIR)


def init_trades_csv():
    if os.path.exists(TRADES_CSV):
        return
    with open(TRADES_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "ticket", "symbol", "strategy", "direction", "volume",
            "open_time_utc", "entry_price", "sl_price", "tp_price",
            "close_time_utc", "exit_price", "exit_reason",
            "duration_minutes", "realized_pnl_usd",
            "entry_spread", "entry_atr", "entry_bid_ask_spread_pct",
            "max_floating_pnl_seen", "min_floating_pnl_seen",
        ])


# ============================================================================
# Log rotation — opens.jsonl appends forever, so rotate when it gets large
# ============================================================================
ROTATE_THRESHOLD_BYTES = 50 * 1024 * 1024   # 50 MB
ROTATE_KEEP_COUNT      = 5                  # keep .1 through .5


def maybe_rotate_opens_log():
    """If opens.jsonl is bigger than ROTATE_THRESHOLD_BYTES, rotate it:
       opens.jsonl.5 ← discarded
       opens.jsonl.4 → .5
       opens.jsonl.3 → .4
       opens.jsonl.2 → .3
       opens.jsonl.1 → .2
       opens.jsonl   → .1
       create fresh empty opens.jsonl"""
    if not os.path.exists(OPENS_JSONL):
        return
    try:
        size = os.path.getsize(OPENS_JSONL)
    except OSError:
        return
    if size < ROTATE_THRESHOLD_BYTES:
        return

    print(f"[rotation] opens.jsonl is {size/1024/1024:.1f}MB, rotating...")
    # Discard the oldest
    oldest = f"{OPENS_JSONL}.{ROTATE_KEEP_COUNT}"
    if os.path.exists(oldest):
        try:
            os.remove(oldest)
        except OSError as e:
            print(f"[rotation] could not remove {oldest}: {e}")

    # Slide each .N → .N+1
    for i in range(ROTATE_KEEP_COUNT - 1, 0, -1):
        src = f"{OPENS_JSONL}.{i}"
        dst = f"{OPENS_JSONL}.{i+1}"
        if os.path.exists(src):
            try:
                os.rename(src, dst)
            except OSError as e:
                print(f"[rotation] could not rename {src} -> {dst}: {e}")

    # Current file becomes .1
    try:
        os.rename(OPENS_JSONL, f"{OPENS_JSONL}.1")
        print(f"[rotation] opens.jsonl rotated to opens.jsonl.1")
    except OSError as e:
        print(f"[rotation] could not rename {OPENS_JSONL}: {e}")


# ============================================================================
# MT5 helpers
# ============================================================================
def bot_positions():
    pos = mt5.positions_get() or []
    return [p for p in pos if p.comment and p.comment.strip() in KNOWN_STRATS]


def symbol_atr14_d1(sym):
    """Quick D1 ATR for context capture at entry."""
    bars = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 16)
    if bars is None or len(bars) < 15:
        return None
    trs = []
    for k in range(1, len(bars) - 1):  # use closed bars
        h = bars[k]['high']; l = bars[k]['low']; pc = bars[k-1]['close']
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else None


def market_snapshot(symbol):
    """Capture current market state — used at open events."""
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if not tick or not info:
        return {}
    spread = tick.ask - tick.bid
    atr_d1 = symbol_atr14_d1(symbol)
    mid = (tick.ask + tick.bid) / 2
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "bid": tick.bid,
        "ask": tick.ask,
        "spread_price": spread,
        "spread_pct_of_price": (spread / mid * 100.0) if mid else 0.0,
        "atr_d1": atr_d1,
    }


# ============================================================================
# Open / close event handlers
# ============================================================================
def on_position_open(p):
    """A new bot position appeared. Snapshot the market and log it."""
    snap = market_snapshot(p.symbol)
    opened = {
        "ticket": p.ticket,
        "symbol": p.symbol,
        "strategy": p.comment.strip(),
        "direction": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
        "volume": p.volume,
        "open_time_utc": datetime.fromtimestamp(p.time, tz=timezone.utc).isoformat(),
        "entry_price": p.price_open,
        "sl_price": p.sl,
        "tp_price": p.tp,
        "snapshot": snap,
        "max_floating": p.profit,
        "min_floating": p.profit,
    }
    state["tracked_opens"][p.ticket] = opened
    # Append to opens.jsonl for permanent record
    try:
        with open(OPENS_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(opened, default=str) + "\n")
    except Exception:
        pass
    print(f"[opened] #{p.ticket} {p.symbol} {opened['direction']} {p.volume}@{p.price_open}  "
          f"spread={snap.get('spread_price', 0):.5f}")


def on_position_progress(p):
    """Position still open — update max/min floating tracker."""
    entry = state["tracked_opens"].get(p.ticket)
    if entry is None: return
    floating = p.profit + p.swap
    entry["max_floating"] = max(entry["max_floating"], floating)
    entry["min_floating"] = min(entry["min_floating"], floating)


def on_position_close(ticket, closed_pnl, close_time, close_price, reason):
    """Position closed — finalize and append to trades.csv."""
    entry = state["tracked_opens"].pop(ticket, None)
    if entry is None:
        # We didn't see this open — skip (e.g. manually opened, or before agent started)
        return
    open_dt = datetime.fromisoformat(entry["open_time_utc"].replace("Z", "+00:00")
                                      if entry["open_time_utc"].endswith("Z")
                                      else entry["open_time_utc"])
    close_dt = datetime.fromtimestamp(close_time, tz=timezone.utc)
    duration_min = (close_dt - open_dt).total_seconds() / 60.0
    try:
        with open(TRADES_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                ticket,
                entry["symbol"],
                entry["strategy"],
                entry["direction"],
                entry["volume"],
                entry["open_time_utc"],
                entry["entry_price"],
                entry["sl_price"],
                entry["tp_price"],
                close_dt.isoformat(),
                close_price,
                reason,
                f"{duration_min:.1f}",
                f"{closed_pnl:.2f}",
                entry["snapshot"].get("spread_price", ""),
                entry["snapshot"].get("atr_d1", ""),
                entry["snapshot"].get("spread_pct_of_price", ""),
                f"{entry['max_floating']:.2f}",
                f"{entry['min_floating']:.2f}",
            ])
    except Exception as e:
        print(f"[err] writing trade record: {e}")
    print(f"[closed] #{ticket} {entry['symbol']} {entry['strategy']} "
          f"pnl=${closed_pnl:+.2f}  duration={duration_min:.0f}min  reason={reason}")


# ============================================================================
# Main tick — discover opens and closes
# ============================================================================
def tick():
    current_positions = bot_positions()
    current_tickets = {p.ticket for p in current_positions}

    # Detect NEW opens
    for p in current_positions:
        if p.ticket not in state["tracked_opens"]:
            on_position_open(p)
        else:
            on_position_progress(p)

    # Detect closures by diff
    known = set(state["tracked_opens"].keys())
    closed_tickets = known - current_tickets
    if closed_tickets:
        # Pull recent deals to find their realized P&L.
        # CRITICAL: filter for DEAL_ENTRY_OUT (the closing leg) to get correct
        # close time/price/reason. Without this, we may pick up the OPEN deal
        # which causes negative duration and pnl=$0.
        from_ts = int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp())
        to_ts = int(datetime.now(timezone.utc).timestamp())
        deals = mt5.history_deals_get(from_ts, to_ts) or []
        for tk in closed_tickets:
            realized = 0.0
            close_time = None
            close_price = 0.0
            reason = "unknown"
            close_comment = ""
            for d in deals:
                if d.position_id != tk:
                    continue
                # Sum P&L from ALL legs (open + close + commissions)
                realized += d.profit + d.swap + getattr(d, "commission", 0.0)
                # But only use the OUT (closing) deal for time/price/reason
                if d.entry == mt5.DEAL_ENTRY_OUT:
                    if close_time is None or d.time > close_time:
                        close_time = d.time
                        close_price = d.price
                        close_comment = (d.comment or "").lower()
            # Infer reason from the CLOSE deal's comment
            if "tp" in close_comment:           reason = "TP"
            elif "sl" in close_comment:         reason = "SL"
            elif "bucket" in close_comment:     reason = "bucket"
            elif "emrg" in close_comment:       reason = "emergency"
            elif "agent" in close_comment:      reason = "agent_close"
            elif "close" in close_comment:      reason = "close"
            elif realized > 0:                  reason = "TP"
            elif realized < 0:                  reason = "SL"
            # Fallback if no OUT deal was found yet (race with broker)
            if close_time is None:
                close_time = to_ts
            on_position_close(tk, realized, close_time, close_price, reason)


# ============================================================================
# Aggregate reports
# ============================================================================
def load_trades():
    rows = []
    if not os.path.exists(TRADES_CSV):
        return rows
    with open(TRADES_CSV, encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                row["realized_pnl_usd"] = float(row["realized_pnl_usd"])
                row["duration_minutes"] = float(row["duration_minutes"])
                rows.append(row)
            except Exception:
                pass
    return rows


def build_per_strategy_csv(trades):
    by_s = defaultdict(list)
    for t in trades:
        by_s[t["strategy"]].append(t)
    with open(BY_STRATEGY_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["strategy", "trades", "wins", "losses", "win_pct",
                    "net_pnl", "avg_pnl", "avg_win", "avg_loss",
                    "best_trade", "worst_trade",
                    "expected_wr", "expected_avg_pnl_per_50",
                    "edge_health"])
        for strat, ts in sorted(by_s.items()):
            pnls = [t["realized_pnl_usd"] for t in ts]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            n = len(pnls)
            wr = 100 * len(wins) / n if n else 0
            net = sum(pnls)
            exp = BACKTEST_EXPECTATION.get(strat, {})
            health = "n/a"
            if exp and n >= 10:
                if wr < exp.get("wr", 0) * 0.7:
                    health = "BELOW_BACKTEST"
                elif wr > exp.get("wr", 0) * 1.3:
                    health = "ABOVE_BACKTEST"
                else:
                    health = "MATCH"
            w.writerow([
                strat, n, len(wins), len(losses), f"{wr:.1f}",
                f"{net:.2f}",
                f"{net/n:.2f}" if n else "0",
                f"{sum(wins)/len(wins):.2f}" if wins else "0",
                f"{sum(losses)/len(losses):.2f}" if losses else "0",
                f"{max(pnls):.2f}" if pnls else "0",
                f"{min(pnls):.2f}" if pnls else "0",
                exp.get("wr", ""),
                exp.get("avg_pnl_per_50", ""),
                health,
            ])


def build_per_day_csv(trades):
    by_d = defaultdict(list)
    for t in trades:
        d = t["close_time_utc"][:10]
        by_d[d].append(t)
    with open(BY_DAY_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "trades", "wins", "win_pct", "net_pnl",
                    "best_trade", "worst_trade"])
        for d, ts in sorted(by_d.items()):
            pnls = [t["realized_pnl_usd"] for t in ts]
            wins = sum(1 for p in pnls if p > 0)
            w.writerow([
                d, len(pnls), wins,
                f"{100*wins/len(pnls):.1f}",
                f"{sum(pnls):.2f}",
                f"{max(pnls):.2f}",
                f"{min(pnls):.2f}",
            ])


def build_report(trades):
    lines = []
    lines.append("=" * 80)
    lines.append(f"D1 PORTFOLIO BOT — Analytics Report  (generated {datetime.now(timezone.utc).isoformat()})")
    lines.append("=" * 80)
    if not trades:
        lines.append("No closed trades recorded yet.")
        with open(REPORT_TXT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return

    total_n = len(trades)
    total_pnl = sum(t["realized_pnl_usd"] for t in trades)
    wins = [t for t in trades if t["realized_pnl_usd"] > 0]
    avg_dur = sum(t["duration_minutes"] for t in trades) / total_n

    lines.append("")
    lines.append(f"OVERALL  trades={total_n}  net_pnl=${total_pnl:+.2f}  "
                 f"win_rate={100*len(wins)/total_n:.1f}%  avg_duration={avg_dur:.0f}min")
    lines.append("")
    lines.append("BY STRATEGY:")
    lines.append("-" * 80)
    by_s = defaultdict(list)
    for t in trades:
        by_s[t["strategy"]].append(t)
    for strat, ts in sorted(by_s.items(), key=lambda x: -sum(t["realized_pnl_usd"] for t in x[1])):
        pnls = [t["realized_pnl_usd"] for t in ts]
        net = sum(pnls)
        wr = 100 * sum(1 for p in pnls if p > 0) / len(pnls)
        exp = BACKTEST_EXPECTATION.get(strat, {})
        flag = ""
        if exp and len(pnls) >= 10:
            exp_wr = exp.get("wr", 0)
            if wr < exp_wr * 0.7: flag = "  ⚠ BELOW backtest"
            elif wr > exp_wr * 1.3: flag = "  ✨ ABOVE backtest"
        lines.append(f"  {strat:<16} n={len(pnls):3d}  wr={wr:5.1f}%  "
                     f"net=${net:+8.2f}  avg=${net/len(pnls):+6.2f}{flag}")

    lines.append("")
    lines.append("BY DAY (last 14):")
    lines.append("-" * 80)
    by_d = defaultdict(list)
    for t in trades:
        d = t["close_time_utc"][:10]
        by_d[d].append(t)
    for d in sorted(by_d)[-14:]:
        ts = by_d[d]
        pnls = [t["realized_pnl_usd"] for t in ts]
        net = sum(pnls)
        wr = 100 * sum(1 for p in pnls if p > 0) / len(pnls)
        lines.append(f"  {d}  trades={len(pnls):2d}  wr={wr:5.1f}%  net=${net:+8.2f}")

    lines.append("")
    lines.append("=" * 80)
    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # Also print to stdout
    print("\n".join(lines[-30:]))


def maybe_emit_report():
    now = int(datetime.now(timezone.utc).timestamp())
    if now - state["last_report_ts"] < REPORT_EVERY_SECONDS:
        return
    state["last_report_ts"] = now
    # Check if opens.jsonl needs rotation (cheap stat call)
    maybe_rotate_opens_log()
    trades = load_trades()
    build_per_strategy_csv(trades)
    build_per_day_csv(trades)
    build_report(trades)
    print(f"[analytics] reports refreshed — see {REPORT_TXT}")


# ============================================================================
# Main loop
# ============================================================================
def main():
    print("=" * 60)
    print("Analytics Agent starting")

    # Single-instance lock
    from process_lock import acquire_or_die
    acquire_or_die("analytics_agent")

    ensure_dir()
    init_trades_csv()
    maybe_rotate_opens_log()   # check on startup so we don't append to a huge file

    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}"); return
    acct = mt5.account_info()
    if acct is None:
        print("No account info"); mt5.shutdown(); return
    if acct.login != ACCT_NO:
        print(f"Account mismatch: connected {acct.login} != configured {ACCT_NO}")
        mt5.shutdown(); return

    print(f"Connected to account {acct.login}  equity=${acct.equity:.2f}")
    print(f"Writing analytics to: {ANALYTICS_DIR}/")
    print(f"Polling every {POLL_SECONDS}s, reporting every {REPORT_EVERY_SECONDS}s")
    print("=" * 60)

    # Adopt any existing positions as "tracked opens"
    for p in bot_positions():
        on_position_open(p)
    print(f"Adopted {len(state['tracked_opens'])} existing positions")

    try:
        while True:
            try:
                tick()
                maybe_emit_report()
            except Exception as e:
                print(f"[err] loop exception: {e}")
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nAnalytics agent stopped")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
