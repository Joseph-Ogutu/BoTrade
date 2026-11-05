"""Password hashing for the dashboard login. Standard library only (scrypt), so the script that sets the password can run
on the bare VM host. The password itself is never stored, logged or sent anywhere; only a salted scrypt hash is kept.

Stored form: scrypt$N$r$p$salt_b64$hash_b64. Parameters are inside the string, so they can be raised later without
breaking old hashes. N=2**15, r=8, p=1 uses about 32 MiB for roughly a tenth of a second per check: slow enough to make
offline guessing expensive, cheap enough for a login form that is also rate limited.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

N, R, P = 2**15, 8, 1
MAXMEM = 128 * 1024 * 1024
MIN_LENGTH = 12
MAX_LENGTH = 256
_PREFIX = "scrypt"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _kdf(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=MAXMEM, dklen=32)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    return f"{_PREFIX}${N}${R}${P}${_b64(salt)}${_b64(_kdf(password, salt, N, R, P))}"


def verify_password(password: str, stored: str) -> bool:
    """True only for an exact match. Any malformed, oversized or unexpected input is simply False."""
    if not isinstance(password, str) or not isinstance(stored, str) or len(password) > MAX_LENGTH:
        return False
    parts = stored.strip().split("$")
    if len(parts) != 6 or parts[0] != _PREFIX:
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, want = base64.b64decode(parts[4], validate=True), base64.b64decode(parts[5], validate=True)
        if not (2**14 <= n <= 2**20 and 1 <= r <= 16 and 1 <= p <= 4 and len(salt) >= 8 and len(want) == 32):
            return False
        got = _kdf(password, salt, n, r, p)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def validate_new_password(password: str) -> str | None:
    """Why this password is not acceptable, or None if it is. Deliberately simple: length is what matters."""
    if len(password) < MIN_LENGTH:
        return f"use at least {MIN_LENGTH} characters (a few unrelated words works well)"
    if len(password) > MAX_LENGTH:
        return f"use at most {MAX_LENGTH} characters"
    if len(set(password)) < 5:
        return "too repetitive"
    if password.isdigit():
        return "digits only is too guessable"
    if password.lower() in {"password1234", "passwordpassword", "qwertyuiop12", "123456789012", "botradeconsole"}:
        return "too common"
    return None


def fingerprint(stored: str) -> str:
    """Short stable id of the stored hash: sessions made under an old password stop working when the password changes."""
    return hashlib.sha256(stored.strip().encode()).hexdigest()[:16]
