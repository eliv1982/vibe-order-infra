"""Unit tests for app.core.security and the JWT-related Settings validation.

No database needed - these exercise pure functions and in-memory JWT
encode/decode only.
"""

import time

import jwt
import pytest
from argon2 import extract_parameters
from argon2.low_level import Type
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    normalize_username,
    verify_password,
)


def test_normalize_username_strips_and_lowercases():
    assert normalize_username(" Admin ") == "admin"
    assert normalize_username("ADMIN") == "admin"
    assert normalize_username("admin") == "admin"
    assert normalize_username("  aDmIn  ") == "admin"


def test_hash_password_differs_from_plaintext_and_is_salted():
    password = "SomeStrongPassw0rd!"
    first_hash = hash_password(password)
    second_hash = hash_password(password)

    assert first_hash != password
    assert first_hash != second_hash  # random per-hash salt


def test_verify_password_accepts_correct_password():
    password = "SomeStrongPassw0rd!"
    assert verify_password(password, hash_password(password)) is True


def test_verify_password_rejects_wrong_password():
    password_hash = hash_password("SomeStrongPassw0rd!")
    assert verify_password("TotallyWrongPassword", password_hash) is False


def test_verify_password_rejects_malformed_hash():
    assert verify_password("whatever", "not-a-real-argon2-hash") is False


def test_hash_password_uses_argon2id_with_the_configured_explicit_profile():
    # Uses argon2-cffi's own parameter extraction rather than a brittle
    # full-string comparison, so this doesn't depend on encoding details
    # (e.g. version string) that aren't the point of this test.
    params = extract_parameters(hash_password("SomeStrongPassw0rd!"))

    assert params.type == Type.ID
    assert params.memory_cost == 19_456
    assert params.time_cost == 2
    assert params.parallelism == 1
    assert params.hash_len == 32
    assert params.salt_len == 16


def test_create_and_decode_access_token_roundtrip():
    token, expires_in = create_access_token(subject="42")

    assert expires_in > 0
    payload = decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["type"] == "access"


def test_decode_access_token_rejects_expired_token():
    settings = get_settings()
    now = int(time.time())
    expired_payload = {"sub": "1", "type": "access", "iat": now - 120, "exp": now - 60}
    token = jwt.encode(expired_payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    with pytest.raises(TokenError):
        decode_access_token(token)


def test_decode_access_token_rejects_corrupted_token():
    token, _ = create_access_token(subject="1")
    corrupted = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")

    with pytest.raises(TokenError):
        decode_access_token(corrupted)


def test_decode_access_token_rejects_wrong_signature():
    settings = get_settings()
    now = int(time.time())
    payload = {"sub": "1", "type": "access", "iat": now, "exp": now + 300}
    forged = jwt.encode(
        payload, "a-completely-different-secret-not-matching-app-1234567890",
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenError):
        decode_access_token(forged)


def test_decode_access_token_rejects_wrong_type_claim():
    settings = get_settings()
    now = int(time.time())
    payload = {"sub": "1", "type": "refresh", "iat": now, "exp": now + 300}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    with pytest.raises(TokenError):
        decode_access_token(token)


def test_decode_access_token_rejects_missing_type_claim():
    settings = get_settings()
    now = int(time.time())
    payload = {"sub": "1", "iat": now, "exp": now + 300}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    with pytest.raises(TokenError):
        decode_access_token(token)


def test_decode_access_token_rejects_missing_exp_claim():
    # Without options={"require": [...]}, PyJWT treats an absent `exp` as
    # "nothing to verify" rather than an error - this pins the fix: a token
    # with no expiration at all must never be treated as permanently valid.
    settings = get_settings()
    now = int(time.time())
    payload = {"sub": "1", "type": "access", "iat": now}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    with pytest.raises(TokenError):
        decode_access_token(token)


def test_decode_access_token_rejects_missing_sub_claim():
    settings = get_settings()
    now = int(time.time())
    payload = {"type": "access", "iat": now, "exp": now + 300}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    with pytest.raises(TokenError):
        decode_access_token(token)


def test_decode_access_token_rejects_empty_sub_claim():
    # `require` only checks presence, not content - an explicit empty-string
    # sub is technically "present" and needs its own rejection.
    settings = get_settings()
    now = int(time.time())
    payload = {"sub": "", "type": "access", "iat": now, "exp": now + 300}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    with pytest.raises(TokenError):
        decode_access_token(token)


def test_decode_access_token_rejects_non_string_sub_claim():
    settings = get_settings()
    now = int(time.time())
    payload = {"sub": 123, "type": "access", "iat": now, "exp": now + 300}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    with pytest.raises(TokenError):
        decode_access_token(token)


def test_decode_access_token_accepts_a_correct_token_after_claim_hardening():
    token, _ = create_access_token(subject="42")
    payload = decode_access_token(token)

    assert payload["sub"] == "42"
    assert payload["type"] == "access"
    assert "exp" in payload


def test_settings_rejects_missing_jwt_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_settings_rejects_too_short_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "too-short-secret")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_rejects_placeholder_jwt_secret(monkeypatch):
    monkeypatch.setenv(
        "JWT_SECRET_KEY",
        "change_me_to_a_random_64_char_secret_generated_locally_1234567890",
    )
    with pytest.raises(ValidationError):
        Settings()


def test_settings_accepts_a_real_looking_secret(monkeypatch):
    monkeypatch.setenv(
        "JWT_SECRET_KEY", "a-sufficiently-long-and-unique-local-test-secret-value-98765"
    )
    Settings()  # must not raise


def test_settings_rejects_unsupported_jwt_algorithm(monkeypatch):
    monkeypatch.setenv("JWT_ALGORITHM", "none")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_rejects_zero_access_token_expire_minutes(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "0")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_rejects_negative_access_token_expire_minutes(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "-5")
    with pytest.raises(ValidationError):
        Settings()
