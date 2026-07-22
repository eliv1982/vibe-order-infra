"""Unit tests for the test-database safety guard. No PostgreSQL required."""

import pytest

from tests.db_safety_guard import (
    UnsafeTestDatabaseError,
    assert_safe_test_database_url,
    get_test_database_url,
)


def _url(database_name: str) -> str:
    return f"postgresql+psycopg://user:pass@localhost:5432/{database_name}"


@pytest.mark.parametrize("database_name", ["vibe_orders_test", "test_vibe_orders", "vibe_testing"])
def test_accepts_explicit_test_database_names(database_name):
    assert_safe_test_database_url(_url(database_name), production_db_name=None)


@pytest.mark.parametrize("database_name", ["vibe_orders", "postgres", "app", "production"])
def test_rejects_database_names_without_test_marker(database_name):
    with pytest.raises(UnsafeTestDatabaseError):
        assert_safe_test_database_url(_url(database_name), production_db_name=None)


def test_rejects_database_name_matching_production_db_even_with_test_marker():
    with pytest.raises(UnsafeTestDatabaseError):
        assert_safe_test_database_url(
            _url("vibe_orders_test"), production_db_name="vibe_orders_test"
        )


def test_get_test_database_url_has_no_fallback_to_production_config(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod-host/vibe_orders")
    monkeypatch.setenv("POSTGRES_DB", "vibe_orders")
    assert get_test_database_url() is None


def test_get_test_database_url_returns_explicit_value(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", _url("vibe_orders_test"))
    assert get_test_database_url() == _url("vibe_orders_test")
