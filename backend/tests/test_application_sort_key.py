"""Unit tests for the created_at sort-key helper used by
GET /api/applications/prioritized (see app/routes/applications.py).

Pure/no DB: exercises the private sort-key helper directly against plain
datetime values, including edge cases (naive, None, mixed timezones) that
are awkward to pin down deterministically against a live PostgreSQL-backed
test. Importing app.routes.applications only needs the placeholder env vars
conftest.py sets at collection time - no database connection is opened.
"""

from datetime import datetime, timedelta, timezone

from app.routes.applications import _created_at_sort_key


def test_aware_datetimes_sort_ascending():
    earlier = datetime(2024, 1, 1, tzinfo=timezone.utc)
    later = datetime(2024, 6, 1, tzinfo=timezone.utc)
    assert _created_at_sort_key(earlier) < _created_at_sort_key(later)


def test_naive_datetime_is_treated_as_utc():
    naive = datetime(2024, 1, 1, 12, 0, 0)
    aware_equivalent = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert _created_at_sort_key(naive) == _created_at_sort_key(aware_equivalent)


def test_aware_datetime_in_other_timezone_normalizes_to_utc_order():
    utc_moment = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Same absolute instant as utc_moment, just expressed in a +1h zone.
    same_instant_elsewhere = datetime(2024, 1, 1, 13, 0, 0, tzinfo=timezone(timedelta(hours=1)))
    assert _created_at_sort_key(utc_moment) == _created_at_sort_key(same_instant_elsewhere)

    strictly_later = datetime(2024, 1, 1, 13, 1, 0, tzinfo=timezone(timedelta(hours=1)))
    assert _created_at_sort_key(utc_moment) < _created_at_sort_key(strictly_later)


def test_mixed_naive_and_aware_list_sorts_without_raising():
    values = [
        datetime(2024, 3, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 1),  # naive
        datetime(2024, 2, 1, tzinfo=timezone.utc),
    ]
    sorted_values = sorted(values, key=_created_at_sort_key)  # must not raise TypeError
    assert sorted_values == [
        datetime(2024, 1, 1),
        datetime(2024, 2, 1, tzinfo=timezone.utc),
        datetime(2024, 3, 1, tzinfo=timezone.utc),
    ]


def test_none_sorts_after_known_dates():
    known = datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert _created_at_sort_key(known) < _created_at_sort_key(None)


def test_multiple_none_values_are_equal_for_sorting():
    assert _created_at_sort_key(None) == _created_at_sort_key(None)


def test_full_tie_break_falls_through_to_id_when_used_as_composite_key():
    same_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = [(5, same_time), (2, same_time), (9, same_time)]
    ordered = sorted(rows, key=lambda row: (_created_at_sort_key(row[1]), row[0]))
    assert [row[0] for row in ordered] == [2, 5, 9]


def test_none_and_dated_rows_together_keep_dated_rows_first_then_id_order():
    dated = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = [(3, None), (1, dated), (2, None), (4, dated)]
    ordered = sorted(rows, key=lambda row: (_created_at_sort_key(row[1]), row[0]))
    assert [row[0] for row in ordered] == [1, 4, 2, 3]
