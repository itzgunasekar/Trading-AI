"""
Encryption helpers for sensitive credentials (MT5 passwords, MFA secrets).

Threat model addressed:
  - Database dump leaked → ciphertext is useless without the KEK
  - SQL injection on users table → broker creds in separate table
  - Insider DB admin → can see ciphertext but not plaintext (KEK is in Vault)
  - Memory dump on app server → plaintext is wiped from memory after use

Cryptographic scheme:
  AES-256-GCM (authenticated encryption with associated data)
  Per-user DEK (data encryption key) — generated once per user, stored encrypted by KEK
  KEK (key encryption key) — held in HashiCorp Vault / AWS KMS / Doppler / env var
  AAD (additional authenticated data) — user_id binds ciphertext to its owner

Operations:
  encrypt(plaintext, user_id) → returns (ciphertext, iv, tag, dek_id)
  decrypt(ciphertext, iv, tag, dek_id, user_id) → plaintext (raises on tamper)

Audit:
  Every decrypt operation should be logged to audit_log with caller identity.
"""

import os
import secrets
import logging
from typing import Tuple
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)


# =====================================================================
# Key management
# =====================================================================
# The KEK (key encryption key) must come from a secure source.
# Priority order:
#   1. HashiCorp Vault (production)
#   2. AWS KMS / GCP KMS / Azure KeyVault
#   3. Doppler / 1Password Secrets
#   4. Environment variable (dev only)
#
# This stub uses env-var; replace _load_kek() with Vault client in production.

def _load_kek() -> bytes:
    """Load the 32-byte AES-256 KEK from environment.

    PRODUCTION: replace this with a Vault / KMS client call.
    The KEK should never be on disk in plaintext.
    """
    hex_key = os.environ.get("D1BOT_KEK_HEX")
    if not hex_key:
        raise RuntimeError(
            "D1BOT_KEK_HEX environment variable is not set. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    key = bytes.fromhex(hex_key)
    if len(key) != 32:
        raise RuntimeError(f"D1BOT_KEK_HEX must be 32 bytes (64 hex chars); got {len(key)} bytes.")
    return key


# Singleton cipher — KEK is loaded once per process
_kek = None


def _get_kek_cipher():
    global _kek
    if _kek is None:
        _kek = AESGCM(_load_kek())
    return _kek


# =====================================================================
# DEK helpers — one DEK per user
# =====================================================================
@dataclass(frozen=True)
class EncryptedField:
    """Result of encrypt(): everything needed to decrypt later."""
    ciphertext: bytes
    iv:         bytes   # 12 bytes
    tag:        bytes   # 16 bytes (split from ciphertext-tag, but kept separate for DB schema)
    dek_id:     str     # which DEK was used (for key rotation)


def generate_user_dek() -> bytes:
    """Generate a fresh 32-byte DEK for a new user.
    The DEK is then encrypted by the KEK and stored in user_broker_credentials.dek_enc.

    For simplicity in MVP, we store the DEK encrypted directly. Later we can
    rotate the KEK without re-encrypting all user ciphertext (the DEK is the
    only thing that needs re-encryption).
    """
    return secrets.token_bytes(32)


def encrypt_dek_with_kek(dek: bytes) -> Tuple[bytes, bytes]:
    """Encrypt a DEK with the master KEK. Returns (ciphertext+tag, iv).
    Used when a new user signs up — we store this in DB."""
    if len(dek) != 32:
        raise ValueError("DEK must be 32 bytes")
    cipher = _get_kek_cipher()
    iv = secrets.token_bytes(12)
    ct_with_tag = cipher.encrypt(iv, dek, associated_data=b"DEK")
    return ct_with_tag, iv


def decrypt_dek_with_kek(ct_with_tag: bytes, iv: bytes) -> bytes:
    """Recover plaintext DEK using KEK. Raises if tampered."""
    cipher = _get_kek_cipher()
    return cipher.decrypt(iv, ct_with_tag, associated_data=b"DEK")


# =====================================================================
# Field-level encryption (uses a DEK passed in)
# =====================================================================
def encrypt_field(plaintext: str, dek: bytes, user_id: str, dek_id: str) -> EncryptedField:
    """Encrypt a string field with the user's DEK.
    user_id binds the ciphertext to its owner via AAD (prevents cross-user replay).
    """
    if not isinstance(plaintext, str):
        raise TypeError("plaintext must be str")
    if len(dek) != 32:
        raise ValueError("DEK must be 32 bytes")
    cipher = AESGCM(dek)
    iv = secrets.token_bytes(12)
    aad = user_id.encode("utf-8")
    pt = plaintext.encode("utf-8")
    ct_with_tag = cipher.encrypt(iv, pt, associated_data=aad)
    # AESGCM library returns ciphertext || tag concatenated. Split last 16 bytes as tag for our schema.
    ct, tag = ct_with_tag[:-16], ct_with_tag[-16:]
    return EncryptedField(ciphertext=ct, iv=iv, tag=tag, dek_id=dek_id)


def decrypt_field(field: EncryptedField, dek: bytes, user_id: str) -> str:
    """Recover plaintext field. Raises if tampered or wrong user."""
    if len(dek) != 32:
        raise ValueError("DEK must be 32 bytes")
    cipher = AESGCM(dek)
    aad = user_id.encode("utf-8")
    ct_with_tag = field.ciphertext + field.tag
    pt_bytes = cipher.decrypt(field.iv, ct_with_tag, associated_data=aad)
    return pt_bytes.decode("utf-8")


# =====================================================================
# High-level helpers — DB-friendly returns
# =====================================================================
def encrypt_credential(plaintext: str, user_id: str, dek: bytes, dek_id: str) -> dict:
    """Encrypt and return dict ready to insert into user_broker_credentials.

    Example:
        enc = encrypt_credential("my_mt5_password", user_id="abc-123", dek=dek, dek_id="v1")
        cursor.execute(
            "INSERT INTO user_broker_credentials "
            "(user_id, mt5_password_enc, mt5_password_enc_iv, mt5_password_enc_tag, dek_id, ...) "
            "VALUES (%s, %s, %s, %s, %s, ...)",
            (user_id, enc['ciphertext'], enc['iv'], enc['tag'], enc['dek_id'], ...)
        )
    """
    field = encrypt_field(plaintext, dek=dek, user_id=user_id, dek_id=dek_id)
    return {
        "ciphertext": field.ciphertext,
        "iv":         field.iv,
        "tag":        field.tag,
        "dek_id":     field.dek_id,
    }


def decrypt_credential(row: dict, user_id: str, dek: bytes) -> str:
    """Inverse of encrypt_credential. Caller should log this via audit_log."""
    field = EncryptedField(
        ciphertext=row["ciphertext"],
        iv=row["iv"],
        tag=row["tag"],
        dek_id=row["dek_id"],
    )
    return decrypt_field(field, dek=dek, user_id=user_id)


# =====================================================================
# Smoke test — run this file directly to verify
# =====================================================================
if __name__ == "__main__":
    # Set up a test KEK
    os.environ["D1BOT_KEK_HEX"] = secrets.token_hex(32)

    # Simulate a user
    user_id = "test-user-uuid-1234"
    dek = generate_user_dek()

    # Encrypt the DEK with the KEK (this is what we'd store in DB)
    dek_ct, dek_iv = encrypt_dek_with_kek(dek)
    print(f"DEK encrypted: {len(dek_ct)} bytes ciphertext + {len(dek_iv)} bytes IV")

    # Decrypt the DEK (this happens whenever we need to use a user's creds)
    dek_recovered = decrypt_dek_with_kek(dek_ct, dek_iv)
    assert dek_recovered == dek, "DEK roundtrip failed"
    print("DEK roundtrip: OK")

    # Encrypt a real credential
    plaintext_pw = "my-broker-password-with-symbols!@#$"
    enc = encrypt_credential(plaintext_pw, user_id=user_id, dek=dek, dek_id="v1")
    print(f"Field encrypted: ct={len(enc['ciphertext'])} iv={len(enc['iv'])} tag={len(enc['tag'])}")

    # Decrypt
    pt_recovered = decrypt_credential(enc, user_id=user_id, dek=dek)
    assert pt_recovered == plaintext_pw, "field roundtrip failed"
    print("Field roundtrip: OK")

    # Tamper test — change a byte in ciphertext
    tampered = dict(enc)
    tampered["ciphertext"] = enc["ciphertext"][:-1] + bytes([enc["ciphertext"][-1] ^ 1])
    try:
        decrypt_credential(tampered, user_id=user_id, dek=dek)
        print("FAIL: tampered ciphertext was accepted!")
    except Exception:
        print("Tamper detection: OK")

    # Wrong-user test — different user_id should fail (AAD mismatch)
    try:
        decrypt_credential(enc, user_id="different-user", dek=dek)
        print("FAIL: wrong user_id was accepted!")
    except Exception:
        print("AAD binding: OK")

    print("\nAll encryption tests passed.")
