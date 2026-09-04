"""Stage 5 correction: focused tests for tests/stage2_db_helpers.py::libpq_url.

Pure string-transformation tests - no TEST_DATABASE_URL/live PostgreSQL
needed. Correctness is checked by actually parsing the rendered string with
libpq itself (via psycopg's compiled binary extension, which links real
libpq - see psycopg.pq.__impl__ == "binary"), not by re-implementing a
second parser here and comparing against it. This is what caught the
original bug: SQLAlchemy's URL.render_as_string encodes query values with
urllib's quote_plus (space -> "+"), and libpq's own URI parser does not
decode "+" back to a space in the query part - confirmed empirically below,
not just asserted from documentation.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import URL

from tests.stage2_db_helpers import libpq_url


def _parsed(url: URL) -> dict[str, str]:
    """Round-trip through libpq's own conninfo parser (psycopg.pq is the
    "binary" implementation - a compiled extension linking real libpq, not a
    pure-Python reimplementation - see psycopg.pq.__impl__)."""
    assert psycopg.pq.__impl__ == "binary", (
        "this test's guarantee depends on parsing with real libpq, not a pure-Python "
        f"stand-in - got psycopg.pq.__impl__={psycopg.pq.__impl__!r}"
    )
    return conninfo_to_dict(libpq_url(url))


def test_ordinary_url():
    url = URL.create(
        drivername="postgresql+psycopg",
        username="vibe_migrator",
        password="plainpassword",
        host="localhost",
        port=5432,
        database="vibe_orders",
    )
    parsed = _parsed(url)
    assert parsed == {
        "user": "vibe_migrator",
        "password": "plainpassword",
        "host": "localhost",
        "port": "5432",
        "dbname": "vibe_orders",
    }


def test_encoded_password_with_reserved_characters():
    # Reserved/URI-special characters libpq must receive verbatim, not
    # mangled by percent-decoding gone wrong.
    password = "p@ss/w:rd#1+2 3"
    url = URL.create(
        drivername="postgresql+psycopg",
        username="vibe_app",
        password=password,
        host="localhost",
        port=5432,
        database="vibe_orders",
    )
    parsed = _parsed(url)
    assert parsed["password"] == password
    assert parsed["user"] == "vibe_app"


def test_ipv6_host_is_bracketed_and_preserved():
    url = URL.create(
        drivername="postgresql+psycopg",
        username="vibe_app",
        password="pw",
        host="::1",
        port=5432,
        database="vibe_orders",
    )
    rendered = libpq_url(url)
    assert "[::1]" in rendered
    parsed = _parsed(url)
    assert parsed["host"] == "::1"
    assert parsed["dbname"] == "vibe_orders"


def test_query_value_containing_a_space_is_percent_encoded_not_plus():
    # The actual regression: SQLAlchemy's own render_as_string() would
    # encode this as "...=-c+search_path%3Dvibe" - a literal "+" libpq does
    # NOT decode as a space in the query part of a URI (unlike a web
    # form/SQLAlchemy's own convention) - see libpq_url's docstring.
    url = URL.create(
        drivername="postgresql+psycopg",
        username="vibe_app",
        password="pw",
        host="localhost",
        port=5432,
        database="vibe_orders",
        query={"options": "-c search_path=vibe"},
    )
    rendered = libpq_url(url)
    assert "+" not in rendered.split("?", 1)[1]
    assert "search_path%3Dvibe" in rendered or "search_path=vibe" in rendered

    parsed = _parsed(url)
    # This is the actual proof: libpq decoded the query value back to
    # exactly the original string, spaces included - not "...+vibe".
    assert parsed["options"] == "-c search_path=vibe"


def test_query_multiple_keys_sorted_and_encoded():
    url = URL.create(
        drivername="postgresql+psycopg",
        username="vibe_app",
        password="pw",
        host="localhost",
        port=5432,
        database="vibe_orders",
        query={"sslmode": "verify-full", "connect_timeout": "10"},
    )
    parsed = _parsed(url)
    assert parsed["sslmode"] == "verify-full"
    assert parsed["connect_timeout"] == "10"


def test_no_query_string_renders_without_trailing_question_mark():
    url = URL.create(
        drivername="postgresql+psycopg",
        username="vibe_app",
        password="pw",
        host="localhost",
        port=5432,
        database="vibe_orders",
    )
    assert "?" not in libpq_url(url)


@pytest.mark.parametrize("password", ["", "simple", "with space", "with+plus"])
def test_various_passwords_round_trip_through_real_libpq(password):
    url = URL.create(
        drivername="postgresql+psycopg",
        username="u",
        password=password,
        host="localhost",
        port=5432,
        database="db",
    )
    parsed = _parsed(url)
    assert parsed.get("password", "") == password
