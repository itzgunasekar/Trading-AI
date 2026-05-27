"""Stripe billing — setup intent, webhook handler, daily fee calculation."""

import os
import hashlib
import json
from decimal import Decimal, ROUND_HALF_EVEN
from datetime import datetime, timezone, timedelta

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, Header

from api.routes.auth import current_user
from db import conn

router = APIRouter(prefix="/billing", tags=["billing"])

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")


# ------------------------------------------------------------
# POST /billing/setup-intent
# Returns a Stripe SetupIntent client secret so the frontend can collect a payment method.
# ------------------------------------------------------------
@router.post("/setup-intent")
def setup_intent(user=Depends(current_user)):
    if not stripe.api_key:
        raise HTTPException(503, "billing not configured")
    user_id = user["user_id"]
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT email FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "user not found")
    # Idempotent customer create
    try:
        customers = stripe.Customer.list(email=row["email"], limit=1).data
        if customers:
            customer = customers[0]
        else:
            customer = stripe.Customer.create(email=row["email"], metadata={"user_id": user_id})
    except stripe.error.StripeError as e:
        raise HTTPException(502, f"stripe error: {e.user_message or str(e)}")

    si = stripe.SetupIntent.create(
        customer=customer.id,
        usage="off_session",
        payment_method_types=["card"],
    )
    return {"client_secret": si.client_secret, "customer_id": customer.id}


# ------------------------------------------------------------
# POST /billing/webhook
# Stripe signs webhook payloads — verify before trusting.
# ------------------------------------------------------------
@router.post("/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    if not stripe.api_key:
        raise HTTPException(503, "billing not configured")
    payload = await request.body()
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(400, "invalid signature")

    et = event["type"]
    obj = event["data"]["object"]

    if et == "invoice.payment_succeeded":
        invoice_id = obj["id"]
        with conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """UPDATE fees SET charge_status = 'paid', paid_at = NOW()
                       WHERE stripe_invoice_id = %s""",
                    (invoice_id,),
                )
            c.commit()

    elif et == "invoice.payment_failed":
        invoice_id = obj["id"]
        with conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """UPDATE fees SET charge_status = 'failed',
                           retry_count = retry_count + 1,
                           next_retry_at = NOW() + INTERVAL '1 hour',
                           failure_reason = %s
                       WHERE stripe_invoice_id = %s RETURNING user_id, retry_count""",
                    (obj.get("last_finalization_error", {}).get("message", "payment failed"), invoice_id),
                )
                row = cur.fetchone()
                # After 3 failures → pause the bot
                if row and row["retry_count"] >= 3:
                    cur.execute(
                        """UPDATE user_configs SET bot_status = 'paused_unpaid',
                           bot_paused_reason = 'payment failed 3 times'
                           WHERE user_id = %s""",
                        (row["user_id"],),
                    )
                    cur.execute(
                        """INSERT INTO audit_log (target_user_id, action, metadata)
                           VALUES (%s, 'system.bot_paused_unpaid', '{"after_retries": 3}'::jsonb)""",
                        (row["user_id"],),
                    )
            c.commit()

    return {"received": True}


# ------------------------------------------------------------
# Internal helper — compute fee for one user for one period.
# Called by daily cron.
# ------------------------------------------------------------
def compute_daily_fee_for_user(user_id: str, day_start_utc: datetime) -> dict:
    """Compute and persist (but do not yet charge) the fee for one user for the
    given UTC day. Returns the fee row dict or None if no fee due."""
    day_end_utc = day_start_utc + timedelta(days=1)
    with conn() as c:
        with c.cursor() as cur:
            # Idempotency: if a row already exists for this period, return it
            cur.execute(
                """SELECT * FROM fees
                   WHERE user_id = %s AND period_start = %s AND period_end = %s""",
                (user_id, day_start_utc, day_end_utc),
            )
            existing = cur.fetchone()
            if existing:
                return dict(existing)

            # Pull user's fee% and cap
            cur.execute(
                """SELECT fee_pct_of_profit, fee_daily_cap_usd FROM user_configs WHERE user_id = %s""",
                (user_id,),
            )
            cfg = cur.fetchone()
            if not cfg:
                return None
            fee_pct = Decimal(str(cfg["fee_pct_of_profit"]))
            daily_cap = cfg.get("fee_daily_cap_usd")

            # Sum realized P&L for closes inside the period
            cur.execute(
                """SELECT COALESCE(SUM(realized_pnl_usd), 0) AS pnl, COUNT(*) AS n
                   FROM trades
                   WHERE user_id = %s
                     AND close_time >= %s AND close_time < %s
                     AND realized_pnl_usd IS NOT NULL""",
                (user_id, day_start_utc, day_end_utc),
            )
            r = cur.fetchone()
            pnl = Decimal(str(r["pnl"] or 0))
            n_trades = r["n"]

            if pnl <= 0:
                # Nothing to charge today
                return None

            # Compute fee (banker's rounding)
            fee = (pnl * fee_pct / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
            if daily_cap and fee > Decimal(str(daily_cap)):
                fee = Decimal(str(daily_cap))

            # Hash of trade IDs that compose this fee (audit anchor)
            cur.execute(
                """SELECT trade_id::text FROM trades
                   WHERE user_id = %s AND close_time >= %s AND close_time < %s
                   ORDER BY trade_id""",
                (user_id, day_start_utc, day_end_utc),
            )
            ids = [r["trade_id"] for r in cur.fetchall()]
            h = hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()

            # Insert fee row (pending) — Stripe charge happens separately
            cur.execute(
                """INSERT INTO fees
                   (user_id, period_start, period_end, realized_pnl_usd, fee_pct,
                    fee_amount_usd, charge_status, pnl_source_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)
                   RETURNING *""",
                (user_id, day_start_utc, day_end_utc, pnl, fee_pct, fee, h),
            )
            row = cur.fetchone()
        c.commit()
    return dict(row)
