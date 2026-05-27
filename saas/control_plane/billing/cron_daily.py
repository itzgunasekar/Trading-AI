"""Daily fee-calculation cron.

Schedule: every day at 00:00 UTC (or whatever you configure).
For each approved user:
  1. Compute realized P&L from yesterday's closed trades
  2. If P&L > 0, create a Fee row and Stripe Invoice
  3. Charge the customer's default payment method
  4. On failure → webhook handler will eventually pause the bot

Usage:
    python -m billing.cron_daily         # process previous UTC day
    python -m billing.cron_daily 2026-05-21    # process specific date
"""

import os
import sys
import logging
from datetime import datetime, timedelta, timezone

import stripe

from api.routes.billing import compute_daily_fee_for_user
from db import conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cron_daily")

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")


def process(day_start: datetime) -> dict:
    log.info(f"Processing fees for {day_start.isoformat()}")
    summary = {"users": 0, "fees_created": 0, "invoiced": 0, "errors": 0, "skipped_no_pnl": 0}
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT u.user_id, u.email
                   FROM users u
                   JOIN user_configs uc ON uc.user_id = u.user_id
                   WHERE u.status = 'approved'
                     AND uc.bot_status NOT IN ('stopped','banned')"""
            )
            users = cur.fetchall()

    for u in users:
        summary["users"] += 1
        try:
            fee = compute_daily_fee_for_user(u["user_id"], day_start)
            if fee is None:
                summary["skipped_no_pnl"] += 1
                continue
            if fee["charge_status"] != "pending":
                continue   # already processed
            summary["fees_created"] += 1

            # Lookup customer
            customers = stripe.Customer.list(email=u["email"], limit=1).data
            if not customers:
                log.warning(f"No Stripe customer for {u['email']} — skip")
                continue
            customer = customers[0]
            pm_id = customer.invoice_settings.default_payment_method
            if not pm_id:
                log.warning(f"No default payment method for {u['email']} — skip")
                continue

            # Create invoice item
            stripe.InvoiceItem.create(
                customer=customer.id,
                amount=int(float(fee["fee_amount_usd"]) * 100),
                currency="usd",
                description=f"D1 Portfolio performance fee {day_start.date().isoformat()}",
            )
            inv = stripe.Invoice.create(
                customer=customer.id,
                auto_advance=True,
                collection_method="charge_automatically",
            )
            inv = stripe.Invoice.finalize_invoice(inv.id)

            # Save invoice ID
            with conn() as c:
                with c.cursor() as cur:
                    cur.execute(
                        "UPDATE fees SET stripe_invoice_id = %s, charge_status = 'processing' WHERE fee_id = %s",
                        (inv.id, fee["fee_id"]),
                    )
                c.commit()
            summary["invoiced"] += 1

        except Exception as e:
            summary["errors"] += 1
            log.exception(f"Failed for {u['email']}: {e}")

    log.info(f"Summary: {summary}")
    return summary


if __name__ == "__main__":
    if len(sys.argv) > 1:
        day = datetime.fromisoformat(sys.argv[1]).replace(tzinfo=timezone.utc, hour=0, minute=0, second=0, microsecond=0)
    else:
        # Default: previous UTC day
        now = datetime.now(timezone.utc)
        day = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    process(day)
