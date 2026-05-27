"""User-facing endpoints — broker creds, dashboard, symbol whitelist."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.routes.auth import current_user
from security.encryption import encrypt_credential, decrypt_dek_with_kek
from db import conn

router = APIRouter(prefix="/user", tags=["user"])


# ------------------------------------------------------------
# POST /user/broker
# Stores MT5 credentials (encrypted) for the authenticated user.
# ------------------------------------------------------------
class BrokerReq(BaseModel):
    broker: str = Field(..., pattern="^(ic_markets|pepperstone|tickmill|fp_markets|exness|other)$")
    mt5_account_no: int = Field(..., ge=1)
    mt5_server: str = Field(..., min_length=2, max_length=64)
    mt5_password: str = Field(..., min_length=1, max_length=128)
    # Optional second broker just for crypto
    crypto_broker: Optional[str] = None
    crypto_account_no: Optional[int] = None
    crypto_password: Optional[str] = None


@router.post("/broker")
def save_broker(req: BrokerReq, user=Depends(current_user)):
    user_id = user["user_id"]
    with conn() as c:
        with c.cursor() as cur:
            # Read this user's encrypted DEK (created at signup)
            cur.execute(
                """SELECT mt5_password_enc AS dek_ct, mt5_password_enc_iv AS dek_iv
                   FROM user_broker_credentials WHERE user_id = %s""",
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "no creds row for user — signup flow broken")

            # Recover DEK by decrypting with KEK
            try:
                dek = decrypt_dek_with_kek(bytes(row["dek_ct"]), bytes(row["dek_iv"]))
            except Exception:
                raise HTTPException(500, "could not unlock user encryption key")

            # Encrypt MT5 password with the user's DEK
            enc = encrypt_credential(req.mt5_password, user_id=user_id, dek=dek, dek_id="v1")

            # If crypto creds present, encrypt those too
            crypto_enc = None
            if req.crypto_password and req.crypto_broker and req.crypto_account_no:
                crypto_enc = encrypt_credential(req.crypto_password, user_id=user_id, dek=dek, dek_id="v1")

            cur.execute(
                """UPDATE user_broker_credentials SET
                       broker = %s::broker_name,
                       mt5_account_no = %s,
                       mt5_server = %s,
                       mt5_password_enc = %s,
                       mt5_password_enc_iv = %s,
                       mt5_password_enc_tag = %s,
                       dek_id = %s,
                       crypto_account_no = %s,
                       crypto_password_enc = %s,
                       crypto_password_iv = %s,
                       crypto_password_tag = %s,
                       crypto_broker = %s,
                       last_validated_at = NULL,
                       last_validation_error = NULL
                   WHERE user_id = %s""",
                (
                    req.broker,
                    req.mt5_account_no,
                    req.mt5_server,
                    enc["ciphertext"], enc["iv"], enc["tag"], enc["dek_id"],
                    req.crypto_account_no,
                    crypto_enc["ciphertext"] if crypto_enc else None,
                    crypto_enc["iv"]         if crypto_enc else None,
                    crypto_enc["tag"]        if crypto_enc else None,
                    req.crypto_broker,
                    user_id,
                ),
            )
            cur.execute(
                """INSERT INTO audit_log (actor_user_id, target_user_id, action, metadata)
                   VALUES (%s, %s, 'user.set_broker', %s::jsonb)""",
                (user_id, user_id, f'{{"broker": "{req.broker}"}}'),
            )
        c.commit()
    return {"status": "saved"}


# ------------------------------------------------------------
# PATCH /user/symbols
# ------------------------------------------------------------
class SymbolsReq(BaseModel):
    symbol_whitelist: List[str] = Field(..., min_length=1, max_length=30)


@router.patch("/symbols")
def update_symbols(req: SymbolsReq, user=Depends(current_user)):
    user_id = user["user_id"]
    # Sanity-check symbol names (whitelist enforcement happens in bot)
    cleaned = [s.upper().strip() for s in req.symbol_whitelist if s.strip()]
    if not cleaned:
        raise HTTPException(400, "empty symbol list")
    import json
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE user_configs SET symbol_whitelist = %s::jsonb WHERE user_id = %s",
                (json.dumps(cleaned), user_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "config not found")
            cur.execute(
                """INSERT INTO audit_log (actor_user_id, target_user_id, action, metadata)
                   VALUES (%s, %s, 'user.update_symbols', %s::jsonb)""",
                (user_id, user_id, json.dumps({"symbols": cleaned})),
            )
        c.commit()
    return {"symbol_whitelist": cleaned}


# ------------------------------------------------------------
# GET /user/dashboard — open positions + recent trades + fees
# ------------------------------------------------------------
@router.get("/dashboard")
def get_dashboard(user=Depends(current_user)):
    user_id = user["user_id"]
    with conn() as c:
        with c.cursor() as cur:
            # Bot status
            cur.execute(
                """SELECT bot_status, fee_pct_of_profit, symbol_whitelist
                   FROM user_configs WHERE user_id = %s""",
                (user_id,),
            )
            cfg = cur.fetchone() or {}

            # Open positions
            cur.execute(
                """SELECT symbol, direction, volume, open_time, entry_price,
                          max_floating_pnl, min_floating_pnl
                   FROM trades WHERE user_id = %s AND close_time IS NULL
                   ORDER BY open_time DESC""",
                (user_id,),
            )
            open_positions = cur.fetchall()

            # Last 50 closed
            cur.execute(
                """SELECT symbol, direction, open_time, close_time, realized_pnl_usd
                   FROM trades WHERE user_id = %s AND close_time IS NOT NULL
                   ORDER BY close_time DESC LIMIT 50""",
                (user_id,),
            )
            recent = cur.fetchall()

            # Fees this period (today)
            cur.execute(
                """SELECT COALESCE(SUM(fee_amount_usd), 0) AS today_fees
                   FROM fees WHERE user_id = %s
                     AND period_end >= date_trunc('day', NOW())""",
                (user_id,),
            )
            today_fees = (cur.fetchone() or {}).get("today_fees", 0)

    return {
        "bot_status":  cfg.get("bot_status") if cfg else "stopped",
        "fee_pct":     float(cfg["fee_pct_of_profit"]) if cfg else None,
        "symbols":     cfg.get("symbol_whitelist") if cfg else [],
        "open_positions": open_positions,
        "recent":      recent,
        "fees_today":  float(today_fees),
    }
