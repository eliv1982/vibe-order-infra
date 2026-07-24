"""Password hashing, username normalization, and JWT helpers for admin authentication.

Kept free of FastAPI/DB imports (mirrors core/exceptions.py) so it can be unit
tested in isolation and reused by both the CRUD layer and route dependencies.
Never logs or stores a plaintext password anywhere.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

from app.core.config import get_settings

TOKEN_TYPE_ACCESS = "access"

# Explicit Argon2id profile - deliberately not relying on argon2-cffi's
# built-in defaults, which are tuned for a generic host and can change
# between library releases. These values target this project's backend
# container (docker-compose.yml caps it at 192M / reserves 128M): ~19 MiB
# per hash/verify call at parallelism=1 leaves headroom for a handful of
# concurrent requests without threatening the container limit. Revisit and
# re-benchmark if that memory limit changes or hashing shows up as a
# measured bottleneck.
_ARGON2_TYPE = Type.ID
_ARGON2_MEMORY_COST_KIB = 19_456
_ARGON2_TIME_COST = 2
_ARGON2_PARALLELISM = 1
_ARGON2_HASH_LEN = 32
_ARGON2_SALT_LEN = 16

_password_hasher = PasswordHasher(
    time_cost=_ARGON2_TIME_COST,
    memory_cost=_ARGON2_MEMORY_COST_KIB,
    parallelism=_ARGON2_PARALLELISM,
    hash_len=_ARGON2_HASH_LEN,
    salt_len=_ARGON2_SALT_LEN,
    type=_ARGON2_TYPE,
)

# Precomputed once at import time so login() always has a hash to verify
# against, even when the supplied username doesn't exist - keeps response
# timing the same whether the username is real or not (see routes/auth.py).
DUMMY_PASSWORD_HASH = _password_hasher.hash("not-a-real-password-used-only-for-timing-safety")


def normalize_username(username: str) -> str:
    """Trim surrounding whitespace and fold case so 'Admin' and 'admin' collide."""
    return username.strip().lower()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


class TokenError(Exception):
    """Raised when a JWT is missing, malformed, expired, forged, or the wrong type."""


def create_access_token(subject: str) -> tuple[str, int]:
    """Return (encoded_jwt, expires_in_seconds) for the given subject (admin id)."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_delta = timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": subject,
        "type": TOKEN_TYPE_ACCESS,
        "iat": now,
        "exp": now + expires_delta,
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an access token, raising TokenError on any problem.

    `options={"require": [...]}` makes PyJWT itself reject a token that's
    missing exp/sub/type outright, instead of silently treating an absent
    claim as "nothing to check" (e.g. a token with no `exp` at all would
    otherwise never be recognized as expired). The algorithm is always taken
    from server-side settings, never from the token's own header. A single
    broad `jwt.PyJWTError` catch covers missing claims, expired signatures,
    bad signatures, and malformed structure alike, so no path from a bad
    token reaches the caller as anything other than TokenError, and no
    internal reason leaks past that generic message.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid or expired token") from exc

    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise TokenError("Unexpected token type")

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise TokenError("Missing or invalid subject claim")

    return payload
