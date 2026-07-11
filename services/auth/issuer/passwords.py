"""argon2id password hashing for the embedded issuer.

OWASP-recommended algorithm and parameters. argon2-cffi handles constant-time
verification and automatic rehash detection (so callers can upgrade old hashes
on successful login).

Parameters are conservative defaults tuned for ~250-500ms of CPU on a modern
laptop. Tune via the env vars below if your deployment has different
requirements.
"""

from __future__ import annotations

import os

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError, VerificationError

_TIME_COST = int(os.environ.get("AUTH_LOCAL_ARGON2_TIME_COST", "3"))
_MEMORY_COST_KIB = int(os.environ.get("AUTH_LOCAL_ARGON2_MEMORY_KIB", str(64 * 1024)))
_PARALLELISM = int(os.environ.get("AUTH_LOCAL_ARGON2_PARALLELISM", "4"))

_HASHER = PasswordHasher(
    time_cost=_TIME_COST,
    memory_cost=_MEMORY_COST_KIB,
    parallelism=_PARALLELISM,
    hash_len=32,
    salt_len=16,
)


def hash_password(plaintext: str) -> str:
    """Argon2id hash of the plaintext. Returns the standard PHC-format string."""
    if not plaintext:
        raise ValueError("password cannot be empty")
    return _HASHER.hash(plaintext)


def verify_password(plaintext: str, hash_str: str) -> bool:
    """Constant-time verify. Returns False on any mismatch or malformed hash."""
    if not plaintext or not hash_str:
        return False
    try:
        return _HASHER.verify(hash_str, plaintext)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False


def needs_rehash(hash_str: str) -> bool:
    """True if the stored hash uses outdated parameters and should be upgraded."""
    try:
        return _HASHER.check_needs_rehash(hash_str)
    except InvalidHash:
        return True
