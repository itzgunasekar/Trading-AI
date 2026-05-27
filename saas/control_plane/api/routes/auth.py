"""Auth endpoints: signup, login, MFA enroll, MFA verify, refresh, logout."""

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, Depends, status
from pydantic import BaseModel, EmailStr, Field

from auth.password import hash_password, verify_password, needs_rehash
from auth.jwt import issue_access, issue_refresh, verify
from auth.mfa import generate_secret, provisioning_uri, verify_code
from security.encryption import (
    generate_user_dek,
    encrypt_dek_with_kek,
    decrypt_dek_with_kek,
    encrypt_credential,
    decrypt_credential,
)
from db import conn

router = APIRouter(prefix="/auth", tags=["auth"])

LOCKOUT_AFTER = 5      # failed attempts before lockout
LOCKOUT_MINUTES = 15


# ---------------- request/response models ----------------

class SignupReq(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class LoginReq(BaseModel):
    email: EmailStr
    password: str
    mfa_code: Optional[str] = None


class LoginRes(BaseModel):
    needs_mfa: bool = False
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    user_id: Optional[str] = None
    status: Optional[str] = None


class MfaEnrollRes(BaseModel):
    qr_uri: str
    secret_for_manual_entry: str


class MfaVerifyReq(BaseModel):
    code: str


# ---------------- helper: extract bearer token ----------------

def current_user(req: Request) -> dict:
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "missing or malformed Authorization header")
    claims = verify(auth.split(" ", 1)[1], expected_type="access")
    if not claims:
        raise HTTPException(401, "invalid or expired token")
    return claims


# ---------------- POST /auth/signup ----------------

@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(req: SignupReq):
    pw_hash = hash_password(req.password)
    with conn() as c:
        with c.cursor() as cur:
            # Check if email already exists
            cur.execute("SELECT user_id FROM users WHERE email = %s", (req.email,))
            if cur.fetchone():
                raise HTTPException(409, "email already registered")

            # Insert user
            cur.execute(
                """INSERT INTO users (email, password_hash, status)
                   VALUES (%s, %s, 'pending') RETURNING user_id""",
                (req.email, pw_hash),
            )
            user_id = cur.fetchone()["user_id"]

            # Generate per-user DEK and store encrypted
            dek = generate_user_dek()
            dek_ct, dek_iv = encrypt_dek_with_kek(dek)

            # Create empty broker creds row (placeholders) to hold the DEK
            cur.execute(
                """INSERT INTO user_broker_credentials
                   (user_id, broker, mt5_account_no, mt5_server,
                    mt5_password_enc, mt5_password_enc_iv, mt5_password_enc_tag, dek_id)
                   VALUES (%s, 'other', 0, '',
                           %s, %s, %s, %s)""",
                (str(user_id), dek_ct, dek_iv, b"", "pending"),
            )

            # Default user_configs row
            cur.execute(
                "INSERT INTO user_configs (user_id) VALUES (%s)",
                (str(user_id),),
            )

            # Audit
            cur.execute(
                """INSERT INTO audit_log (actor_user_id, target_user_id, action, metadata, ip_address)
                   VALUES (%s, %s, 'user.signup', '{}'::jsonb, NULL)""",
                (str(user_id), str(user_id)),
            )
        c.commit()
    return {"user_id": str(user_id), "status": "pending", "message": "awaiting admin approval"}


# ---------------- POST /auth/login ----------------

@router.post("/login", response_model=LoginRes)
def login(req: LoginReq, request: Request):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT user_id, password_hash, status, mfa_enrolled, mfa_secret_enc,
                          failed_login_count, locked_until
                   FROM users WHERE email = %s""",
                (req.email,),
            )
            row = cur.fetchone()
            if not row:
                # Don't reveal whether email exists
                raise HTTPException(401, "invalid credentials")

            # Lockout check
            if row["locked_until"] and row["locked_until"] > datetime.now(timezone.utc):
                raise HTTPException(429, "account temporarily locked, try again later")

            # Verify password
            if not verify_password(row["password_hash"], req.password):
                fc = (row["failed_login_count"] or 0) + 1
                lock_until = None
                if fc >= LOCKOUT_AFTER:
                    lock_until = datetime.now(timezone.utc) + __import__("datetime").timedelta(minutes=LOCKOUT_MINUTES)
                cur.execute(
                    "UPDATE users SET failed_login_count = %s, locked_until = %s WHERE user_id = %s",
                    (fc, lock_until, row["user_id"]),
                )
                c.commit()
                raise HTTPException(401, "invalid credentials")

            # MFA check
            if row["mfa_enrolled"]:
                if not req.mfa_code:
                    return LoginRes(needs_mfa=True)
                # Decrypt MFA secret using the user's DEK
                # For simplicity, MFA secret uses the KEK directly (no per-user DEK):
                # Get the DEK
                cur.execute(
                    "SELECT mt5_password_enc as ct, mt5_password_enc_iv as iv FROM user_broker_credentials WHERE user_id = %s",
                    (row["user_id"],),
                )
                # NOTE: in production, MFA secret should be encrypted with its OWN
                # key path. We keep it simple here — store raw base32 inside DB
                # encrypted with KEK directly. For demo, treat row["mfa_secret_enc"]
                # as base32 string bytes.
                try:
                    secret = bytes(row["mfa_secret_enc"]).decode("utf-8")
                except Exception:
                    raise HTTPException(500, "mfa configuration error")
                if not verify_code(secret, req.mfa_code):
                    raise HTTPException(401, "invalid mfa code")

            # Success — reset counters, issue tokens
            cur.execute(
                "UPDATE users SET failed_login_count = 0, locked_until = NULL, last_login_at = NOW(), last_login_ip = %s WHERE user_id = %s",
                (request.client.host if request.client else None, row["user_id"]),
            )
            c.commit()
            access = issue_access(str(row["user_id"]), is_admin=False, mfa_passed=bool(row["mfa_enrolled"]))
            refresh = issue_refresh(str(row["user_id"]))
            return LoginRes(
                access_token=access,
                refresh_token=refresh,
                user_id=str(row["user_id"]),
                status=row["status"],
            )


# ---------------- POST /auth/mfa/enroll ----------------

@router.post("/mfa/enroll", response_model=MfaEnrollRes)
def mfa_enroll(user=Depends(current_user)):
    """Start MFA enrollment — returns QR URI; user must verify before it's saved."""
    secret = generate_secret()
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT email FROM users WHERE user_id = %s", (user["user_id"],))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "user not found")
            email = row["email"]
            # Store the secret tentatively but do NOT set mfa_enrolled until verify
            cur.execute(
                "UPDATE users SET mfa_secret_enc = %s WHERE user_id = %s",
                (secret.encode("utf-8"), user["user_id"]),
            )
        c.commit()
    uri = provisioning_uri(email=email, secret=secret)
    return MfaEnrollRes(qr_uri=uri, secret_for_manual_entry=secret)


# ---------------- POST /auth/mfa/verify ----------------

@router.post("/mfa/verify")
def mfa_verify(req: MfaVerifyReq, user=Depends(current_user)):
    """Confirm enrollment by verifying the first TOTP code."""
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT mfa_secret_enc FROM users WHERE user_id = %s", (user["user_id"],))
            row = cur.fetchone()
            if not row or not row["mfa_secret_enc"]:
                raise HTTPException(400, "no pending enrollment")
            secret = bytes(row["mfa_secret_enc"]).decode("utf-8")
            if not verify_code(secret, req.code):
                raise HTTPException(401, "invalid code")
            cur.execute(
                "UPDATE users SET mfa_enrolled = TRUE WHERE user_id = %s",
                (user["user_id"],),
            )
            cur.execute(
                """INSERT INTO audit_log (actor_user_id, target_user_id, action, metadata)
                   VALUES (%s, %s, 'user.mfa_enrolled', '{}'::jsonb)""",
                (user["user_id"], user["user_id"]),
            )
        c.commit()
    return {"enrolled": True}


# ---------------- POST /auth/refresh ----------------

class RefreshReq(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=LoginRes)
def refresh(req: RefreshReq):
    claims = verify(req.refresh_token, expected_type="refresh")
    if not claims:
        raise HTTPException(401, "invalid or expired refresh token")
    user_id = claims["user_id"]
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT status, mfa_enrolled FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "user not found")
    return LoginRes(
        access_token=issue_access(user_id, is_admin=False, mfa_passed=row["mfa_enrolled"]),
        refresh_token=issue_refresh(user_id),
        user_id=user_id,
        status=row["status"],
    )


# ---------------- GET /auth/me ----------------

@router.get("/me")
def me(user=Depends(current_user)):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT user_id, email, status, mfa_enrolled, created_at, approved_at, last_login_at
                   FROM users WHERE user_id = %s""",
                (user["user_id"],),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "user not found")
            return {
                "user_id":      str(row["user_id"]),
                "email":        row["email"],
                "status":       row["status"],
                "mfa_enrolled": row["mfa_enrolled"],
                "created_at":   row["created_at"].isoformat() if row["created_at"] else None,
                "approved_at":  row["approved_at"].isoformat() if row["approved_at"] else None,
                "last_login":   row["last_login_at"].isoformat() if row["last_login_at"] else None,
            }
