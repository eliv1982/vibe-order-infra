"""Pydantic-only tests for the auth schemas: no database needed."""

import pytest
from pydantic import ValidationError

from app.schemas.auth import AdminLogin, AdminRegister


def test_admin_register_normalizes_username():
    payload = AdminRegister(username="  Admin  ", password="StrongPassw0rd!")
    assert payload.username == "admin"


def test_admin_register_rejects_short_password():
    with pytest.raises(ValidationError):
        AdminRegister(username="admin", password="short1")


def test_admin_register_never_trims_or_alters_password():
    payload = AdminRegister(username="admin", password="  StrongPassw0rd!  ")
    assert payload.password == "  StrongPassw0rd!  "


def test_admin_register_rejects_whitespace_only_username():
    # Raw length (3 spaces) would pass min_length=3 if length were checked
    # before normalization; normalized it's "", which must be rejected.
    with pytest.raises(ValidationError):
        AdminRegister(username="   ", password="StrongPassw0rd!")


def test_admin_register_rejects_username_too_short_after_normalization():
    # "  ab  " is 6 raw characters (>= min_length=3) but normalizes to "ab"
    # (2 chars) - length must be checked against the normalized value.
    with pytest.raises(ValidationError):
        AdminRegister(username="  ab  ", password="StrongPassw0rd!")


def test_admin_register_normalizes_valid_username_with_surrounding_whitespace():
    payload = AdminRegister(username="  admin123  ", password="StrongPassw0rd!")
    assert payload.username == "admin123"


def test_admin_login_normalizes_username():
    payload = AdminLogin(username="ADMIN", password="whatever")
    assert payload.username == "admin"


def test_admin_login_rejects_whitespace_only_username():
    with pytest.raises(ValidationError):
        AdminLogin(username="   ", password="whatever")


def test_admin_login_password_is_never_trimmed_or_altered():
    payload = AdminLogin(username="admin", password="  whatever  ")
    assert payload.password == "  whatever  "


def test_admin_login_does_not_enforce_registration_password_policy():
    # Login must accept whatever's typed and let the hash comparison decide -
    # applying today's strength rule retroactively could lock out an admin
    # created under different rules.
    payload = AdminLogin(username="admin", password="x")
    assert payload.password == "x"


def test_admin_login_rejects_empty_password():
    with pytest.raises(ValidationError):
        AdminLogin(username="admin", password="")


def test_admin_login_rejects_empty_username():
    with pytest.raises(ValidationError):
        AdminLogin(username="", password="whatever")
