"""Schema-level validation tests for the analytics response models.

No database or network access required — these always run, including when
TEST_DATABASE_URL is not set. Only the public schema models are exercised
here (never Pydantic internals) — the aggregation logic itself is
unit-tested separately in test_behavior_analytics_service.py.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.analytics import AnalyticsOverview, ApplicationBehaviorAnalyticsRead, SectionAnalyticsItem

_PERIOD_START = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
_PERIOD_END = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


def _overview_kwargs(**overrides) -> dict:
    base = dict(
        period="week",
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
        applications_count=0,
        metrics_count=0,
        applications_with_metrics=0,
        applications_without_metrics=0,
        average_time_on_page_seconds=None,
        median_time_on_page_seconds=None,
        average_return_count=None,
        total_return_count=0,
        total_button_clicks=0,
        unique_clicked_buttons=0,
    )
    base.update(overrides)
    return base


def _detail_kwargs(**overrides) -> dict:
    base = dict(
        application_id=1,
        has_metrics=True,
        time_on_page_seconds=None,
        return_count=None,
        total_button_clicks=0,
    )
    base.update(overrides)
    return base


# --- AnalyticsOverview: average_time_on_page_seconds ------------------------


def test_overview_average_time_on_page_accepts_none():
    overview = AnalyticsOverview(**_overview_kwargs(average_time_on_page_seconds=None))
    assert overview.average_time_on_page_seconds is None


def test_overview_average_time_on_page_accepts_zero():
    overview = AnalyticsOverview(**_overview_kwargs(average_time_on_page_seconds=0))
    assert overview.average_time_on_page_seconds == 0


def test_overview_average_time_on_page_accepts_positive():
    overview = AnalyticsOverview(**_overview_kwargs(average_time_on_page_seconds=42.5))
    assert overview.average_time_on_page_seconds == 42.5


def test_overview_average_time_on_page_rejects_negative():
    with pytest.raises(ValidationError):
        AnalyticsOverview(**_overview_kwargs(average_time_on_page_seconds=-1))


# --- AnalyticsOverview: median_time_on_page_seconds -------------------------


def test_overview_median_time_on_page_accepts_none():
    overview = AnalyticsOverview(**_overview_kwargs(median_time_on_page_seconds=None))
    assert overview.median_time_on_page_seconds is None


def test_overview_median_time_on_page_accepts_zero():
    overview = AnalyticsOverview(**_overview_kwargs(median_time_on_page_seconds=0))
    assert overview.median_time_on_page_seconds == 0


def test_overview_median_time_on_page_accepts_positive():
    overview = AnalyticsOverview(**_overview_kwargs(median_time_on_page_seconds=10))
    assert overview.median_time_on_page_seconds == 10


def test_overview_median_time_on_page_rejects_negative():
    with pytest.raises(ValidationError):
        AnalyticsOverview(**_overview_kwargs(median_time_on_page_seconds=-0.01))


# --- AnalyticsOverview: average_return_count ---------------------------


def test_overview_average_return_count_accepts_none():
    overview = AnalyticsOverview(**_overview_kwargs(average_return_count=None))
    assert overview.average_return_count is None


def test_overview_average_return_count_accepts_zero():
    overview = AnalyticsOverview(**_overview_kwargs(average_return_count=0))
    assert overview.average_return_count == 0


def test_overview_average_return_count_accepts_positive():
    overview = AnalyticsOverview(**_overview_kwargs(average_return_count=3.5))
    assert overview.average_return_count == 3.5


def test_overview_average_return_count_rejects_negative():
    with pytest.raises(ValidationError):
        AnalyticsOverview(**_overview_kwargs(average_return_count=-2))


# --- SectionAnalyticsItem: duration fields -----------------------------


def _section_kwargs(**overrides) -> dict:
    base = dict(
        section="hero",
        total_duration_seconds=0,
        average_duration_seconds=0,
        interactions_count=0,
        share_percent=0,
    )
    base.update(overrides)
    return base


def test_section_total_duration_accepts_zero_and_positive():
    assert SectionAnalyticsItem(**_section_kwargs(total_duration_seconds=0)).total_duration_seconds == 0
    assert SectionAnalyticsItem(**_section_kwargs(total_duration_seconds=1.5)).total_duration_seconds == 1.5


def test_section_total_duration_rejects_negative():
    with pytest.raises(ValidationError):
        SectionAnalyticsItem(**_section_kwargs(total_duration_seconds=-0.5))


def test_section_average_duration_rejects_negative():
    with pytest.raises(ValidationError):
        SectionAnalyticsItem(**_section_kwargs(average_duration_seconds=-1))


def test_section_share_percent_still_bounded_0_to_100():
    with pytest.raises(ValidationError):
        SectionAnalyticsItem(**_section_kwargs(share_percent=-1))
    with pytest.raises(ValidationError):
        SectionAnalyticsItem(**_section_kwargs(share_percent=100.1))
    assert SectionAnalyticsItem(**_section_kwargs(share_percent=100)).share_percent == 100


# --- ApplicationBehaviorAnalyticsRead: time_on_page_seconds -----------------


def test_detail_time_on_page_accepts_none():
    detail = ApplicationBehaviorAnalyticsRead(**_detail_kwargs(time_on_page_seconds=None))
    assert detail.time_on_page_seconds is None


def test_detail_time_on_page_accepts_zero():
    detail = ApplicationBehaviorAnalyticsRead(**_detail_kwargs(time_on_page_seconds=0))
    assert detail.time_on_page_seconds == 0


def test_detail_time_on_page_accepts_positive():
    detail = ApplicationBehaviorAnalyticsRead(**_detail_kwargs(time_on_page_seconds=99.9))
    assert detail.time_on_page_seconds == 99.9


def test_detail_time_on_page_rejects_negative():
    with pytest.raises(ValidationError):
        ApplicationBehaviorAnalyticsRead(**_detail_kwargs(time_on_page_seconds=-0.01))


# --- ApplicationBehaviorAnalyticsRead: return_count -----------------------


def test_detail_return_count_accepts_none():
    detail = ApplicationBehaviorAnalyticsRead(**_detail_kwargs(return_count=None))
    assert detail.return_count is None


def test_detail_return_count_accepts_zero():
    detail = ApplicationBehaviorAnalyticsRead(**_detail_kwargs(return_count=0))
    assert detail.return_count == 0


def test_detail_return_count_accepts_positive():
    detail = ApplicationBehaviorAnalyticsRead(**_detail_kwargs(return_count=5))
    assert detail.return_count == 5


def test_detail_return_count_rejects_negative():
    with pytest.raises(ValidationError):
        ApplicationBehaviorAnalyticsRead(**_detail_kwargs(return_count=-1))
