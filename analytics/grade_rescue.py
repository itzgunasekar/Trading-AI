"""
Grade Rescue Layer decisions against actual trade outcomes.

Reads `logs/rescue_decisions.csv` (written every time the rescue scorer evaluates
a near-SL loser) and joins each row with the trade's eventual realized P&L from
MT5 history.

Reports:
  - Of decisions marked "keep": fraction that ended profitable
  - Avg P&L of keeps that recovered vs keeps that ultimately hit SL
  - Score-bucket calibration: did high-score decisions outperform low-score ones?
  - $ impact: total realized $ from kept trades vs. what LEGACY (-0.7R close)
    would have realized for the same set

Usage:  python analytics/grade_rescue.py
"""

import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import MetaTrader5 as mt5

LOG_FILE = os.path.join("logs", "rescue_decisions.csv")


def load_decisions():
    if not os.path.exists(LOG_FILE):
        print(f"  no decisions yet at {LOG_FILE} — run the bot for a few days first.")
        return []
    rows = []
    with open(LOG_FILE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                r["ticket"] = int(r["ticket"])
                r["score"] = int(r["score"])
                r["consumed_pct"] = float(r["consumed_pct"])
                rows.append(r)
            except (ValueError, KeyError):
                continue
    return rows


def fetch_realized_pnl(tickets):
    """Return {ticket: realized_pnl} from MT5 deal history. Tickets still open → omitted."""
    if not tickets:
        return {}
    if not mt5.initialize():
        print("MT5 init failed:", mt5.last_error())
        sys.exit(1)
    # Pull a generous history window — last 90 days covers most cases
    from_ts = int((datetime.now(timezone.utc).timestamp()) - 90 * 86400)
    to_ts = int(datetime.now(timezone.utc).timestamp())
    deals = mt5.history_deals_get(from_ts, to_ts) or []
    by_ticket = defaultdict(float)
    closed = set()
    for d in deals:
        if d.position_id in tickets:
            by_ticket[d.position_id] += d.profit + d.swap + getattr(d, "commission", 0.0)
            if d.entry == mt5.DEAL_ENTRY_OUT:
                closed.add(d.position_id)
    return {t: by_ticket[t] for t in closed}


def main():
    rows = load_decisions()
    if not rows:
        return
    print(f"loaded {len(rows)} decision rows from {LOG_FILE}")
    tickets = {r["ticket"] for r in rows}
    print(f"unique tickets: {len(tickets)}")
    realized = fetch_realized_pnl(tickets)
    print(f"closed tickets with realized P&L available: {len(realized)}\n")

    # De-dup: the same ticket may have many decisions logged over its life
    # (re-evaluated every bucket-fire). Use the LAST decision per ticket.
    last_decision = {}
    for r in rows:
        prev = last_decision.get(r["ticket"])
        if prev is None or r["ts"] > prev["ts"]:
            last_decision[r["ticket"]] = r

    keeps = [r for r in last_decision.values() if r["action"] == "keep"]
    closes = [r for r in last_decision.values() if r["action"] == "close"]

    # ---- Grade KEEP decisions ----
    print("=" * 76)
    print(f"KEEP DECISIONS  —  {len(keeps)} unique tickets evaluated as 'keep'")
    print("=" * 76)
    if not keeps:
        print("  (none)")
    else:
        graded = [(r, realized[r["ticket"]]) for r in keeps if r["ticket"] in realized]
        ungraded = [r for r in keeps if r["ticket"] not in realized]
        print(f"  closed and graded: {len(graded)}    still open: {len(ungraded)}")
        if graded:
            n_pos = sum(1 for _, p in graded if p > 0)
            n_neg = len(graded) - n_pos
            total_pnl = sum(p for _, p in graded)
            wr = 100.0 * n_pos / len(graded)
            print(f"  ended profitable: {n_pos} ({wr:.1f}%)   ended loss: {n_neg}")
            print(f"  total realized $: {total_pnl:+,.2f}")
            if graded:
                avg = total_pnl / len(graded)
                print(f"  avg realized $/trade: {avg:+.2f}")

            # Legacy counterfactual: what if we had closed every keep at -0.7R?
            # Approximate R = average abs P&L of losing keeps (proxy for risk size)
            losers = [p for _, p in graded if p < 0]
            if losers:
                R = abs(sum(losers) / len(losers))
                # Trades that ended SL: assume they hit at full -R; legacy would have closed at -0.7R
                legacy_total = -0.7 * R * len(graded)
                rescue_edge = total_pnl - legacy_total
                print(f"  approximate R (avg losing-keep): ${R:.2f}")
                print(f"  legacy counterfactual (-0.7R × {len(graded)}): ${legacy_total:+,.2f}")
                print(f"  RESCUE edge over LEGACY on these tickets: ${rescue_edge:+,.2f}")

            # Score-bucket calibration: do higher scores correlate with better outcomes?
            print("\n  Score-bucket calibration:")
            buckets = [(0, 45), (45, 55), (55, 65), (65, 80), (80, 101)]
            for lo, hi in buckets:
                sub = [(r, p) for r, p in graded if lo <= r["score"] < hi]
                if not sub:
                    continue
                sub_pos = sum(1 for _, p in sub if p > 0)
                sub_pnl = sum(p for _, p in sub)
                print(f"    score [{lo:>3}-{hi:<3}): n={len(sub):>3}  "
                      f"profitable={sub_pos:>3} ({100*sub_pos/len(sub):>5.1f}%)  "
                      f"total=${sub_pnl:>+8.2f}")

    # ---- Grade CLOSE decisions ----
    print("\n" + "=" * 76)
    print(f"CLOSE DECISIONS  —  {len(closes)} unique tickets force-closed by rescue layer")
    print("=" * 76)
    if not closes:
        print("  (none)")
    else:
        graded_c = [(r, realized[r["ticket"]]) for r in closes if r["ticket"] in realized]
        if graded_c:
            avg = sum(p for _, p in graded_c) / len(graded_c)
            total = sum(p for _, p in graded_c)
            print(f"  closed via rescue: {len(graded_c)}")
            print(f"  total realized $:  {total:+,.2f}")
            print(f"  avg per trade:     {avg:+.2f}")
            print("  (CLOSE decisions are not counterfactually graded — once we close")
            print("   the trade we don't observe what 'keeping' would have done.)")

    # ---- Per-strategy breakdown ----
    print("\n" + "=" * 76)
    print("BY STRATEGY (keep decisions only)")
    print("=" * 76)
    by_strat = defaultdict(list)
    for r in keeps:
        if r["ticket"] in realized:
            by_strat[r["strat"]].append((r, realized[r["ticket"]]))
    if not by_strat:
        print("  (no closed kept trades yet)")
    else:
        print(f"  {'Strategy':<16} {'N':>4} {'Win%':>6} {'TotalPnL':>12}")
        print(f"  {'-'*16} {'-'*4} {'-'*6} {'-'*12}")
        for strat, items in sorted(by_strat.items()):
            n_pos = sum(1 for _, p in items if p > 0)
            wr = 100.0 * n_pos / len(items)
            total = sum(p for _, p in items)
            print(f"  {strat:<16} {len(items):>4} {wr:>5.1f}% {total:>+12.2f}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
