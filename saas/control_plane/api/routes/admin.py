"""Admin endpoints — approve users, set fee%, pause/reactivate."""

from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth.jwt import verify
from db import conn

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(req: Request) -> dict:
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "unauthorized")
    claims = verify(auth.split(" ", 1)[1], expected_type="access")
    if not claims or not claims.get("is_admin"):
        raise HTTPException(403, "admin only")
    return claims


# ------------------------------------------------------------
# GET /admin/users
# ------------------------------------------------------------
@router.get("/users")
def list_users(_=Depends(require_admin),
               status: Optional[str] = None,
               limit: int = 100):
    sql = "SELECT * FROM admin_user_summary"
    params: list = []
    if status:
        sql += " WHERE status = %s"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return {"users": rows, "count": len(rows)}


# ------------------------------------------------------------
# POST /admin/users/{user_id}/approve
# ------------------------------------------------------------
@router.post("/users/{user_id}/approve")
def approve_user(user_id: str, admin=Depends(require_admin)):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT status FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "user not found")
            if row["status"] != "pending":
                raise HTTPException(400, f"user is not pending (status={row['status']})")
            cur.execute(
                """UPDATE users SET status = 'approved',
                       approved_at = NOW(),
                       approved_by = %s
                   WHERE user_id = %s""",
                (admin["user_id"], user_id),
            )
            cur.execute(
                """INSERT INTO audit_log (actor_admin_id, target_user_id, action, metadata)
                   VALUES (%s, %s, 'admin.approve_user', '{}'::jsonb)""",
                (admin["user_id"], user_id),
            )
        c.commit()
    return {"user_id": user_id, "status": "approved"}


# ------------------------------------------------------------
# POST /admin/users/{user_id}/reject
# ------------------------------------------------------------
class RejectReq(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


@router.post("/users/{user_id}/reject")
def reject_user(user_id: str, req: RejectReq, admin=Depends(require_admin)):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE users SET status = 'banned' WHERE user_id = %s AND status = 'pending'",
                (user_id,),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "user not found or not pending")
            cur.execute(
                """INSERT INTO audit_log (actor_admin_id, target_user_id, action, metadata)
                   VALUES (%s, %s, 'admin.reject_user', %s::jsonb)""",
                (admin["user_id"], user_id, '{"reason": "' + req.reason.replace('"', '\\"') + '"}'),
            )
        c.commit()
    return {"user_id": user_id, "status": "banned"}


# ------------------------------------------------------------
# PATCH /admin/users/{user_id}/fee
# ------------------------------------------------------------
class SetFeeReq(BaseModel):
    fee_pct_of_profit: float = Field(ge=0, le=100)
    fee_daily_cap_usd: Optional[float] = Field(default=None, ge=0)


@router.patch("/users/{user_id}/fee")
def set_fee(user_id: str, req: SetFeeReq, admin=Depends(require_admin)):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE user_configs
                   SET fee_pct_of_profit = %s,
                       fee_daily_cap_usd = %s,
                       updated_by_admin_id = %s
                   WHERE user_id = %s""",
                (req.fee_pct_of_profit, req.fee_daily_cap_usd, admin["user_id"], user_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "user_configs not found")
            cur.execute(
                """INSERT INTO audit_log (actor_admin_id, target_user_id, action, metadata)
                   VALUES (%s, %s, 'admin.set_fee', %s::jsonb)""",
                (admin["user_id"], user_id, f'{{"fee_pct": {req.fee_pct_of_profit}}}'),
            )
        c.commit()
    return {"user_id": user_id, "fee_pct_of_profit": req.fee_pct_of_profit}


# ------------------------------------------------------------
# POST /admin/users/{user_id}/pause
# ------------------------------------------------------------
@router.post("/users/{user_id}/pause")
def pause_user(user_id: str, admin=Depends(require_admin)):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE user_configs SET bot_status = 'paused_admin', bot_paused_reason = 'admin pause' WHERE user_id = %s",
                (user_id,),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "user not found")
            cur.execute(
                """INSERT INTO audit_log (actor_admin_id, target_user_id, action, metadata)
                   VALUES (%s, %s, 'admin.pause_bot', '{}'::jsonb)""",
                (admin["user_id"], user_id),
            )
        c.commit()
    # TODO: signal bot container to stop accepting new entries
    return {"user_id": user_id, "bot_status": "paused_admin"}


# ------------------------------------------------------------
# POST /admin/users/{user_id}/reactivate
# ------------------------------------------------------------
@router.post("/users/{user_id}/reactivate")
def reactivate_user(user_id: str, admin=Depends(require_admin)):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE user_configs SET bot_status = 'running', bot_paused_reason = NULL WHERE user_id = %s",
                (user_id,),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "user not found")
            cur.execute(
                """INSERT INTO audit_log (actor_admin_id, target_user_id, action, metadata)
                   VALUES (%s, %s, 'admin.reactivate_bot', '{}'::jsonb)""",
                (admin["user_id"], user_id),
            )
        c.commit()
    # TODO: signal bot container to resume
    return {"user_id": user_id, "bot_status": "running"}


# ------------------------------------------------------------
# GET /admin/audit
# ------------------------------------------------------------
@router.get("/audit")
def get_audit(_=Depends(require_admin), limit: int = 200, user_id: Optional[str] = None):
    sql = "SELECT * FROM audit_log"
    params: list = []
    if user_id:
        sql += " WHERE target_user_id = %s OR actor_user_id = %s"
        params.extend([user_id, user_id])
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return {"events": rows, "count": len(rows)}
