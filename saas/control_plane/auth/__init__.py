"""Authentication primitives — password hashing, JWT, MFA."""
from .password import hash_password, verify_password, needs_rehash
