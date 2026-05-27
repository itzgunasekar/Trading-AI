"""
Grade SL-Migration (Break-Even Move + Age-Decay TP-Tighten) decisions
against actual trade outcomes from your own account.

This is the measurement infrastructure for the "should I keep
SL_MIGRATION_TRIGGER = 0.50, push to 0.75, or disable?" question.

Reads:
    logs/sl_migration_events.csv   — one row per BE-move or age-decay event
                                      (written by trade_intelligence.migrate_position_stops)

Joins with MT5 deal history to learn the eventual outcome of each migrated
position, then reports:
    - Per strategy: BE-stop rate, TP-hit rate, avg realized $
    - The KEY counterfactual: how many positions BE-stopped near entry,
      and roughly what % would have hit TP if SL hadn't moved (estimated
      from MFE — we can only approximate this since closing prevents
      observation)
    - Recommendation: keep / push trigger / disable

Usage:  python analytics/grade_sl_migration.py
Safe to run anytime — read-only, no trading actions.
"""

import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import MetaTrader5 as mt5

EVENT_LOG  = os.path.join("logs", "sl_migration_events.csv")
MIN_DECISIONS_FOR_VERDICT = 20   # need this many graded events before opinion is meaningful


# ANSI colors
_TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
G = "\033[92m" if _TTY else ""
R = "\033[91m" if _TTY else ""
Y = "\033[93m" if _TTY else ""
B = "\033[94m" if _TTY else ""
X = "\033[0m"  if _TTY else ""


def load_events():
    """Return list of dicts from the CSV."""
    if not os.path.exists(EVENT_LOG):
        print(f"  no events yet at {EVENT_LOG} — let the bot run for a few days first.")
        return []
    rows = []
    with open(EVENT_LOG, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                r["ticket"]          = int(r["ticket"])
                r["entry_price"]     = float(r["entry_price"])
                r["original_sl"]     = float(r["original_sl"])
                r["new_sl"]          = float(r["new_sl"])
                r["original_tp"]     = float(r["original_tp"])
                r["new_tp"]          = float(r["new_tp"])
                r["price_at_event"]  = float(r["price_at_event"])
                r["favorable_pct_at_event"] = float(r["favorable_pct_at_event"])
                r["age_hours"]       = float(r["age_hours"])
                rows.append(r)
            except (ValueError, KeyError):
                continue
    return rows


def fetch_closed_pnl(tickets):
    """Return {ticket: (realized_pnl, close_price)} from MT5 history.
    Tickets still open are omitted."""
    if not tickets:
        return {}
    if not mt5.initialize():
        print("MT5 init failed:", mt5.last_error())
        sys.exit(1)
    from_ts = int((datetime.now(timezone.utc).timestamp()) - 180 * 86400)
    to_ts   = int(datetime.now(timezone.utc).timestamp())
    deals = mt5.history_deals_get(from_ts, to_ts) or []
    pnl_by_ticket = defaultdict(float)
    close_price = {}
    closed = set()
    for d in deals:
        if d.position_id not in tickets:
            continue
        pnl_by_ticket[d.position_id] += d.profit + d.swap + getattr(d, "commission", 0.0)
        if d.entry == mt5.DEAL_ENTRY_OUT:
            close_price[d.position_id] = d.price
            closed.add(d.position_id)
    return {t: (pnl_by_ticket[t], close_price.get(t, 0.0)) for t in closed}


def classify_outcome(event, realized_pnl, close_price):
    """Decide what category this migrated position ended up in:
        - 'TP-hit'        : closed at or near original TP
        - 'BE-stop'       : closed at or near new (entry+buffer) SL
        - 'mid-exit'      : closed between BE and TP (e.g. age-decayed TP, bucket sweep, agent close)
        - 'reversal-loss' : somehow closed BELOW the new SL (rare — agent emergency or gap)
    """
    entry = event["entry_price"]
    new_sl = event["new_sl"]
    tp     = event["original_tp"]
    direction = event["direction"]

    # How close to TP did we close?
    if direction == "BUY":
        tp_dist = tp - entry
        if tp_dist <= 0: return "unknown"
        progress_to_tp = (close_price - entry) / tp_dist
    else:
        tp_dist = entry - tp
        if tp_dist <= 0: return "unknown"
        progress_to_tp = (entry - close_price) / tp_dist

    # Buckets
    if progress_to_tp >= 0.85:
        return "TP-hit"
    if -0.10 <= progress_to_tp <= 0.10:
        return "BE-stop"
    if progress_to_tp < -0.10:
        return "reversal-loss"   # closed past new SL — agent close or wide gap
    return "mid-exit"             # 10%..85% to TP — partial win


def main():
    rows = load_events()
    if not rows:
        return
    print(f"loaded {len(rows)} migration events from {EVENT_LOG}")

    # De-dup by (ticket, kind) — same ticket may have BE-move AND age-decay events
    last_event = {}
    for r in rows:
        key = (r["ticket"], r["kind"])
        prev = last_event.get(key)
        if prev is None or r["ts"] > prev["ts"]:
            last_event[key] = r

    tickets = {e["ticket"] for e in last_event.values()}
    print(f"unique tickets:    {len(tickets)}")
    realized = fetch_closed_pnl(tickets)
    print(f"closed & graded:   {len(realized)}\n")

    # Split by kind
    be_events  = [e for e in last_event.values() if e["kind"] == "BE-move"]
    age_events = [e for e in last_event.values() if e["kind"] == "age-decay"]

    print("=" * 80)
    print(f"{B}BE-MOVE EVENTS{X}  —  did break-even protection help or hurt?")
    print("=" * 80)
    if not be_events:
        print("  (none yet)")
    else:
        graded = [(e, realized[e["ticket"]]) for e in be_events if e["ticket"] in realized]
        open_  = [e for e in be_events if e["ticket"] not in realized]
        print(f"  graded={len(graded)}    still open={len(open_)}")

        if graded:
            buckets = defaultdict(list)   # outcome -> list of (event, pnl)
            for e, (pnl, price) in graded:
                outcome = classify_outcome(e, pnl, price)
                buckets[outcome].append((e, pnl, price))

            n_tp     = len(buckets.get("TP-hit", []))
            n_be     = len(buckets.get("BE-stop", []))
            n_mid    = len(buckets.get("mid-exit", []))
            n_loss   = len(buckets.get("reversal-loss", []))
            n_total  = len(graded)

            be_pnl   = sum(p for _, p, _ in buckets.get("BE-stop", []))
            tp_pnl   = sum(p for _, p, _ in buckets.get("TP-hit", []))
            mid_pnl  = sum(p for _, p, _ in buckets.get("mid-exit", []))
            loss_pnl = sum(p for _, p, _ in buckets.get("reversal-loss", []))
            total_pnl = be_pnl + tp_pnl + mid_pnl + loss_pnl

            print(f"\n  {'Outcome':<16} {'N':>4} {'%':>6}  {'$ total':>10}  {'avg $/trade':>11}")
            print(f"  {'-'*16} {'-'*4} {'-'*6}  {'-'*10}  {'-'*11}")
            for label, n, total in [
                ("TP-hit",        n_tp,   tp_pnl),
                ("mid-exit",      n_mid,  mid_pnl),
                ("BE-stop",       n_be,   be_pnl),
                ("reversal-loss", n_loss, loss_pnl),
            ]:
                if n == 0: continue
                pct = 100.0 * n / n_total
                avg = total / n if n else 0
                color = G if total > 0 else (R if total < 0 else "")
                print(f"  {label:<16} {n:>4} {pct:>5.1f}%  {color}{total:>+10.2f}{X}  {avg:>+11.2f}")
            print(f"  {'-'*16} {'-'*4} {'-'*6}  {'-'*10}")
            tot_color = G if total_pnl > 0 else R if total_pnl < 0 else ""
            print(f"  {'TOTAL':<16} {n_total:>4} {'100%':>6}  {tot_color}{total_pnl:>+10.2f}{X}")

            # ---- Counterfactual estimate ----
            # If SL hadn't moved, the BE-stops would have either:
            #   (a) continued to TP → would have gained (tp_dist - be_dist) * lot * usd_per_pp
            #       i.e. roughly the full R distance to TP
            #   (b) reversed all the way to original SL → would have lost full -1R
            # We can only ESTIMATE this — actual recovery rate from BE-stops to TP is unknowable.
            # Assume the average BUY trade has 1.5R-3R TP and recovery rate of 35-50%.
            # Use 40% as a midpoint estimate.
            print()
            if n_be > 0:
                # Assume each BE-stop "could have" hit TP with prob = ~35% (mid-range guess)
                # and would have hit -original_SL otherwise
                est_recovery_pct = 35.0
                # Estimate average R-multiple of TP vs SL based on the events themselves
                r_multiples = []
                for e, _, _ in buckets.get("BE-stop", []):
                    entry = e["entry_price"]
                    osl = e["original_sl"]
                    tp = e["original_tp"]
                    sl_dist = abs(entry - osl)
                    tp_dist = abs(tp - entry)
                    if sl_dist > 0:
                        r_multiples.append(tp_dist / sl_dist)
                avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 1.5
                # Avg risk per BE-stop trade approximated from BE-stop magnitude
                # (we know BE losses are tiny, but original risk was avg_R times the win)
                # Use simple expected-value formula:
                avg_be_loss = abs(be_pnl / n_be) if n_be else 0
                # implied R ≈ avg_be_loss × (some factor); fall back to assuming risk = $500 default
                implied_R = 50.0   # conservative; real value depends on lot sizing
                ev_no_be = (est_recovery_pct/100.0) * implied_R * avg_r + \
                           (1 - est_recovery_pct/100.0) * (-implied_R)
                ev_with_be = -avg_be_loss   # what we actually got per BE-stop
                est_lost_per_trade = ev_no_be - ev_with_be
                est_total_lost = est_lost_per_trade * n_be
                print(f"  {Y}Counterfactual estimate{X} (rough, assumes 35% recovery from BE-stop):")
                print(f"     avg BE-stop realized:           ${be_pnl/n_be:+.2f}")
                print(f"     avg R-multiple of TP vs SL:     {avg_r:.2f}")
                print(f"     est. EV/trade if SL hadn't moved: ${ev_no_be:+.2f}")
                print(f"     est. cost of BE protection:     ${est_lost_per_trade:+.2f}/trade")
                print(f"     est. total cost over {n_be} BE-stops: ${est_total_lost:+.2f}")

    print("\n" + "=" * 80)
    print(f"{B}AGE-DECAY TP-TIGHTEN EVENTS{X}  —  did locking partial wins help?")
    print("=" * 80)
    if not age_events:
        print("  (none yet)")
    else:
        graded = [(e, realized[e["ticket"]]) for e in age_events if e["ticket"] in realized]
        if graded:
            wins = sum(1 for e, (pnl, _) in graded if pnl > 0)
            total = sum(pnl for e, (pnl, _) in graded)
            print(f"  graded={len(graded)}  wins={wins} ({100*wins/len(graded):.0f}%)  total=${total:+.2f}")

    # ---- Per-strategy breakdown ----
    print("\n" + "=" * 80)
    print(f"{B}BE-MOVE BY STRATEGY{X}")
    print("=" * 80)
    by_strat = defaultdict(list)
    for e in be_events:
        if e["ticket"] in realized:
            by_strat[e["strat"]].append((e, *realized[e["ticket"]]))
    if not by_strat:
        print("  (no closed BE-moved trades yet)")
    else:
        print(f"  {'Strategy':<16} {'N':>4} {'TP%':>5} {'BE-stop%':>9} {'Total $':>10}")
        print(f"  {'-'*16} {'-'*4} {'-'*5} {'-'*9} {'-'*10}")
        for strat in sorted(by_strat):
            items = by_strat[strat]
            tp_hits  = sum(1 for e, p, pr in items if classify_outcome(e, p, pr) == "TP-hit")
            be_stops = sum(1 for e, p, pr in items if classify_outcome(e, p, pr) == "BE-stop")
            total    = sum(p for _, p, _ in items)
            n = len(items)
            tp_pct = 100*tp_hits/n if n else 0
            be_pct = 100*be_stops/n if n else 0
            print(f"  {strat:<16} {n:>4} {tp_pct:>4.0f}% {be_pct:>8.0f}% {total:>+10.2f}")

    # ---- Recommendation ----
    print("\n" + "=" * 80)
    print(f"{B}RECOMMENDATION{X}")
    print("=" * 80)
    n_be_graded = sum(1 for e in be_events if e["ticket"] in realized)
    if n_be_graded < MIN_DECISIONS_FOR_VERDICT:
        print(f"  {Y}Insufficient data{X} — only {n_be_graded} graded BE events, need ≥ {MIN_DECISIONS_FOR_VERDICT}.")
        print("  Let the bot run another 2-4 weeks and re-run this script.")
    else:
        # The actual decision logic
        be_stop_rate = (len(buckets.get("BE-stop", [])) / n_be_graded) * 100 if be_events else 0
        tp_hit_rate  = (len(buckets.get("TP-hit",  [])) / n_be_graded) * 100 if be_events else 0
        print(f"  BE-stop rate: {be_stop_rate:.1f}%   TP-hit rate after BE move: {tp_hit_rate:.1f}%")
        print()
        if be_stop_rate < 25:
            print(f"  {G}KEEP{X} SL_MIGRATION_TRIGGER = 0.50.")
            print(f"      BE-stop rate is low. The protection rarely costs you a real opportunity.")
        elif be_stop_rate < 45:
            print(f"  {Y}CONSIDER raising{X} SL_MIGRATION_TRIGGER to 0.65 or 0.75.")
            print(f"      BE-stop rate is moderate. Pushing the trigger later means fewer")
            print(f"      BE-stops without giving up the protection entirely.")
        else:
            print(f"  {R}CONSIDER disabling{X} SL_MIGRATION (set USE_SL_MIGRATION = False) or")
            print(f"      pushing SL_MIGRATION_TRIGGER to 0.80+. Current setting is stopping")
            print(f"      out too many trades before they reach TP.")
        print()
        if total_pnl > 0:
            print(f"  Net $ realized on BE-migrated trades: {G}${total_pnl:+,.2f}{X}.")
            print(f"  Migration is contributing positively to your bottom line.")
        else:
            print(f"  Net $ realized on BE-migrated trades: {R}${total_pnl:+,.2f}{X}.")
            print(f"  Migration may be costing money — review carefully before adjusting.")

    mt5.shutdown()


if __name__ == "__main__":
    main()
