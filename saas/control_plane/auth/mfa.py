"""TOTP MFA via pyotp."""

import secrets
import pyotp


def generate_secret() -> str:
    """Return a base32-encoded TOTP secret (suitable for QR code)."""
    return pyotp.random_base32()


def provisioning_uri(email: str, secret: str, issuer: str = "D1 Portfolio") -> str:
    """URI for QR code generation."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_code(secret: str, code: str, window: int = 1) -> bool:
    """Verify a 6-digit TOTP. window=1 allows ±30s clock drift."""
    if not code or not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=window)
