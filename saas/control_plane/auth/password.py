"""Password hashing via argon2id — current OWASP recommendation."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

# Argon2id parameters tuned for ~200ms hash time on a typical server.
# Adjust based on your hardware — re-benchmark periodically.
_ph = PasswordHasher(
    time_cost=3,        # iterations
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(plaintext: str) -> str:
    """Return argon2id hash including all parameters embedded."""
    if not plaintext or len(plaintext) < 12:
        raise ValueError("Password must be at least 12 characters.")
    return _ph.hash(plaintext)


def verify_password(stored_hash: str, candidate: str) -> bool:
    """Constant-time verification. Returns True iff match."""
    try:
        _ph.verify(stored_hash, candidate)
        return True
    except (VerifyMismatchError, InvalidHash):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True if the stored hash uses outdated parameters → rehash on next login."""
    try:
        return _ph.check_needs_rehash(stored_hash)
    except InvalidHash:
        return True
