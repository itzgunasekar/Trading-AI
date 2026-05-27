"""JWT issue and verify — short-lived access + longer-lived refresh."""

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import jwt, JWTError

ACCESS_TTL_MIN = 15
REFRESH_TTL_DAYS = 14
ALGO = "HS256"


def _secret() -> str:
    s = os.environ.get("JWT_SECRET")
    if not s or len(s) < 32:
        raise RuntimeError("JWT_SECRET must be set (32+ chars)")
    return s


def issue_access(user_id: str, is_admin: bool = False, mfa_passed: bool = False) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "user_id":   user_id,
        "is_admin":  is_admin,
        "mfa":       mfa_passed,
        "iat":       int(now.timestamp()),
        "exp":       int((now + timedelta(minutes=ACCESS_TTL_MIN)).timestamp()),
        "type":      "access",
        "jti":       secrets.token_hex(8),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGO)


def issue_refresh(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "iat":     int(now.timestamp()),
        "exp":     int((now + timedelta(days=REFRESH_TTL_DAYS)).timestamp()),
        "type":    "refresh",
        "jti":     secrets.token_hex(8),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGO)


def verify(token: str, expected_type: str = "access") -> Optional[dict]:
    try:
        claims = jwt.decode(token, _secret(), algorithms=[ALGO])
    except JWTError:
        return None
    if claims.get("type") != expected_type:
        return None
    return claims
