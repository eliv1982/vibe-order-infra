"""Unit tests for the pure behavior-analytics service.

No database or network access required - these always run, including when
TEST_DATABASE_URL is not set. build_overview()/build_application_detail()
only read attributes off their inputs, so SimpleNamespace stands in for a
BehaviorMetric/Application without needing SQLAlchemy or a real request.
"""

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.behavior_analytics import (
    build_application_detail,
    build_overview,
    compute_period_bounds,
)

NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)
# Strictly before NOW so it's safely inside every period's half-open window
# [period_start, period_end=NOW) by default; tests targeting the exact
# boundary pass created_at explicitly instead of relying on this default.
IN_PERIOD = NOW - timedelta(hours=1)


def _app(id: int, created_at: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(id=id, created_at=created_at)


def _metric(
    id: int = 1,
    application_id: int | None = 1,
    created_at: datetime | None = IN_PERIOD,
    time_on_page=0,
    clicked_buttons=None,
    cursor_hover_data=None,
    return_count=0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        application_id=application_id,
        created_at=created_at,
        time_on_page=time_on_page,
        clicked_buttons=clicked_buttons if clicked_buttons is not None else [],
        cursor_hover_data=cursor_hover_data if cursor_hover_data is not None else {},
        return_count=return_count,
    )


# --- period bounds -----------------------------------------------------


def test_period_bounds_day_is_24_hours():
    start, end = compute_period_bounds("day", NOW)
    assert end == NOW
    assert start == NOW - timedelta(hours=24)


def test_period_bounds_week_is_7_days():
    start, end = compute_period_bounds("week", NOW)
    assert start == NOW - timedelta(days=7)


def test_period_bounds_month_is_30_rolling_days_not_calendar_month():
    start, end = compute_period_bounds("month", NOW)
    assert start == NOW - timedelta(days=30)


def test_period_lower_bound_is_included():
    start, end = compute_period_bounds("day", NOW)
    application = _app(1, created_at=start)
    overview = build_overview([application], [], period="day", now=NOW)
    assert overview.applications_count == 1


def test_period_end_is_excluded():
    application = _app(1, created_at=NOW)
    overview = build_overview([application], [], period="day", now=NOW)
    assert overview.applications_count == 0


def test_period_just_before_end_is_included():
    just_before = NOW - timedelta(microseconds=1)
    application = _app(1, created_at=just_before)
    overview = build_overview([application], [], period="day", now=NOW)
    assert overview.applications_count == 1


def test_period_just_before_start_is_excluded():
    start, _ = compute_period_bounds("day", NOW)
    application = _app(1, created_at=start - timedelta(microseconds=1))
    overview = build_overview([application], [], period="day", now=NOW)
    assert overview.applications_count == 0


def test_period_bounds_are_timezone_aware_utc():
    start, end = compute_period_bounds("week", NOW)
    assert start.tzinfo is not None
    assert end.tzinfo is not None
    assert start.utcoffset() == timedelta(0)
    assert end.utcoffset() == timedelta(0)


def test_naive_now_is_treated_as_utc_deterministically():
    naive_now = datetime(2026, 7, 25, 12, 0, 0)
    start, end = compute_period_bounds("day", naive_now)
    assert end == NOW
    assert start == NOW - timedelta(hours=24)


def test_naive_created_at_is_treated_as_utc_deterministically():
    naive_created_at = datetime(2026, 7, 25, 11, 0, 0)
    application = _app(1, created_at=naive_created_at)
    overview = build_overview([application], [], period="day", now=NOW)
    assert overview.applications_count == 1


# --- empty data ----------------------------------------------------------


def test_no_applications_no_metrics():
    overview = build_overview([], [], period="week", now=NOW)
    assert overview.applications_count == 0
    assert overview.metrics_count == 0
    assert overview.applications_with_metrics == 0
    assert overview.applications_without_metrics == 0
    assert overview.popular_buttons == ()
    assert overview.section_activity == ()
    assert overview.average_time_on_page_seconds is None
    assert overview.median_time_on_page_seconds is None
    assert overview.average_return_count is None
    assert overview.total_return_count == 0
    assert overview.total_button_clicks == 0
    assert overview.unique_clicked_buttons == 0


def test_applications_without_metrics_when_no_metrics_recorded():
    applications = [_app(1, IN_PERIOD), _app(2, IN_PERIOD)]
    overview = build_overview(applications, [], period="week", now=NOW)
    assert overview.applications_count == 2
    assert overview.applications_with_metrics == 0
    assert overview.applications_without_metrics == 2


# --- time on page ----------------------------------------------------------


def test_time_on_page_average_and_median_odd_count():
    metrics = [_metric(id=i, time_on_page=v) for i, v in enumerate([10, 20, 30], start=1)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_time_on_page_seconds == 20.0
    assert overview.median_time_on_page_seconds == 20.0


def test_time_on_page_median_even_count():
    metrics = [_metric(id=i, time_on_page=v) for i, v in enumerate([10, 20, 30, 40], start=1)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.median_time_on_page_seconds == 25.0


def test_time_on_page_zero_is_valid():
    metrics = [_metric(id=1, time_on_page=0)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_time_on_page_seconds == 0.0


def test_time_on_page_negative_is_ignored():
    metrics = [_metric(id=1, time_on_page=-5), _metric(id=2, time_on_page=10)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_time_on_page_seconds == 10.0


def test_time_on_page_none_is_ignored():
    metrics = [_metric(id=1, time_on_page=None), _metric(id=2, time_on_page=10)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_time_on_page_seconds == 10.0


def test_time_on_page_nan_is_ignored():
    metrics = [_metric(id=1, time_on_page=math.nan), _metric(id=2, time_on_page=10)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_time_on_page_seconds == 10.0


def test_time_on_page_infinity_is_ignored():
    metrics = [_metric(id=1, time_on_page=math.inf), _metric(id=2, time_on_page=10)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_time_on_page_seconds == 10.0


def test_time_on_page_all_invalid_yields_none():
    metrics = [_metric(id=1, time_on_page=None), _metric(id=2, time_on_page=-1)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_time_on_page_seconds is None
    assert overview.median_time_on_page_seconds is None


def test_time_on_page_rounds_to_two_decimals():
    metrics = [_metric(id=i, time_on_page=v) for i, v in enumerate([1, 2, 2], start=1)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_time_on_page_seconds == 1.67


# --- return_count ----------------------------------------------------------


def test_return_count_average_and_total():
    metrics = [_metric(id=i, return_count=v) for i, v in enumerate([1, 2, 3], start=1)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_return_count == 2.0
    assert overview.total_return_count == 6


def test_return_count_zero_is_valid():
    metrics = [_metric(id=1, return_count=0)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_return_count == 0.0
    assert overview.total_return_count == 0


def test_return_count_negative_is_excluded():
    metrics = [_metric(id=1, return_count=-1), _metric(id=2, return_count=5)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_return_count == 5.0
    assert overview.total_return_count == 5


def test_return_count_none_is_excluded():
    metrics = [_metric(id=1, return_count=None), _metric(id=2, return_count=5)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_return_count == 5.0


def test_return_count_malformed_runtime_value_is_excluded():
    metrics = [_metric(id=1, return_count="not-a-number"), _metric(id=2, return_count=3)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_return_count == 3.0
    assert overview.total_return_count == 3


def test_return_count_all_invalid_yields_none_average_and_zero_total():
    metrics = [_metric(id=1, return_count=None), _metric(id=2, return_count=-1)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.average_return_count is None
    assert overview.total_return_count == 0


# --- buttons -----------------------------------------------------------


def test_single_click():
    metrics = [_metric(id=1, clicked_buttons=[{"button": "hero_cta", "count": 1}])]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.popular_buttons[0].name == "hero_cta"
    assert overview.popular_buttons[0].count == 1
    assert overview.total_button_clicks == 1


def test_repeated_clicks_across_metrics_are_summed():
    metrics = [
        _metric(id=1, clicked_buttons=[{"button": "hero_cta", "count": 2}]),
        _metric(id=2, clicked_buttons=[{"button": "hero_cta", "count": 3}]),
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.popular_buttons[0].count == 5
    assert overview.total_button_clicks == 5
    assert overview.unique_clicked_buttons == 1


def test_multiple_distinct_buttons_sorted_count_desc_name_asc():
    metrics = [
        _metric(
            id=1,
            clicked_buttons=[
                {"button": "b_button", "count": 2},
                {"button": "a_button", "count": 2},
                {"button": "z_button", "count": 5},
            ],
        )
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    names = [item.name for item in overview.popular_buttons]
    assert names == ["z_button", "a_button", "b_button"]


def test_button_share_percent():
    metrics = [
        _metric(
            id=1,
            clicked_buttons=[
                {"button": "a", "count": 1},
                {"button": "b", "count": 3},
            ],
        )
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    by_name = {item.name: item.share_percent for item in overview.popular_buttons}
    assert by_name["a"] == 25.0
    assert by_name["b"] == 75.0


def test_button_no_clicks_no_division_by_zero():
    metrics = [_metric(id=1, clicked_buttons=[])]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.popular_buttons == ()
    assert overview.total_button_clicks == 0


def test_buttons_top_10_cap():
    clicked = [{"button": f"btn_{i}", "count": i + 1} for i in range(15)]
    metrics = [_metric(id=1, clicked_buttons=clicked)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert len(overview.popular_buttons) == 10
    # Highest counts (btn_14 has count 15, down to btn_5 with count 6) win the cap.
    assert overview.popular_buttons[0].name == "btn_14"


def test_button_empty_name_is_dropped():
    metrics = [
        _metric(
            id=1,
            clicked_buttons=[{"button": "   ", "count": 5}, {"button": "real", "count": 1}],
        )
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    names = [item.name for item in overview.popular_buttons]
    assert names == ["real"]


def test_button_malformed_shapes_are_skipped_without_crashing():
    metrics = [
        _metric(id=1, clicked_buttons="not-a-list"),
        _metric(id=2, clicked_buttons=None),
        _metric(id=3, clicked_buttons=[None, "just-a-string", 42, {"count": 1}, {"button": 7}]),
        _metric(id=4, clicked_buttons=[{"button": "real", "count": 1}]),
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.total_button_clicks == 1
    assert overview.popular_buttons[0].name == "real"


def test_button_negative_and_non_finite_counts_are_skipped():
    metrics = [
        _metric(
            id=1,
            clicked_buttons=[
                {"button": "neg", "count": -3},
                {"button": "nan", "count": math.nan},
                {"button": "inf", "count": math.inf},
                {"button": "ok", "count": 2},
            ],
        )
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    names = [item.name for item in overview.popular_buttons]
    assert names == ["ok"]


def test_button_zero_count_does_not_increase_total_button_clicks():
    metrics = [_metric(id=1, clicked_buttons=[{"button": "never_clicked", "count": 0}])]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.total_button_clicks == 0


def test_button_zero_count_does_not_increase_unique_clicked_buttons():
    metrics = [_metric(id=1, clicked_buttons=[{"button": "never_clicked", "count": 0}])]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.unique_clicked_buttons == 0


def test_button_zero_count_does_not_appear_in_popular_buttons():
    metrics = [_metric(id=1, clicked_buttons=[{"button": "never_clicked", "count": 0}])]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.popular_buttons == ()


def test_button_zero_count_alongside_real_click_keeps_only_the_real_one():
    metrics = [
        _metric(
            id=1,
            clicked_buttons=[
                {"button": "never_clicked", "count": 0},
                {"button": "really_clicked", "count": 2},
            ],
        )
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    names = [item.name for item in overview.popular_buttons]
    assert names == ["really_clicked"]
    assert overview.total_button_clicks == 2
    assert overview.unique_clicked_buttons == 1


def test_button_zero_malformed_and_negative_counts_are_all_ignored_together():
    metrics = [
        _metric(
            id=1,
            clicked_buttons=[
                {"button": "zero", "count": 0},
                {"button": "malformed", "count": "not-a-number"},
                {"button": "negative", "count": -1},
                {"button": "real", "count": 1},
            ],
        )
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    names = [item.name for item in overview.popular_buttons]
    assert names == ["real"]
    assert overview.total_button_clicks == 1
    assert overview.unique_clicked_buttons == 1


def test_xss_like_button_name_is_kept_as_inert_string():
    payload = "<script>alert(1)</script>"
    metrics = [_metric(id=1, clicked_buttons=[{"button": payload, "count": 1}])]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.popular_buttons[0].name == payload


def test_button_names_are_not_case_merged():
    metrics = [
        _metric(
            id=1,
            clicked_buttons=[
                {"button": "CTA", "count": 1},
                {"button": "cta", "count": 1},
            ],
        )
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.unique_clicked_buttons == 2


# --- sections (cursor_hover_data) -------------------------------------------


def test_section_aggregation_across_metrics():
    metrics = [
        _metric(id=1, cursor_hover_data={"hero": {"hovers": 1, "ms": 1000}}),
        _metric(id=2, cursor_hover_data={"hero": {"hovers": 2, "ms": 500}}),
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    hero = overview.section_activity[0]
    assert hero.section == "hero"
    assert hero.interactions_count == 3
    assert hero.total_duration_seconds == 1.5


def test_section_ms_is_normalized_to_seconds():
    metrics = [_metric(id=1, cursor_hover_data={"hero": {"hovers": 1, "ms": 2500}})]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.section_activity[0].total_duration_seconds == 2.5


def test_section_average_duration():
    metrics = [_metric(id=1, cursor_hover_data={"hero": {"hovers": 2, "ms": 4000}})]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.section_activity[0].average_duration_seconds == 2.0


def test_section_sorting_duration_desc_name_asc():
    metrics = [
        _metric(
            id=1,
            cursor_hover_data={
                "b_section": {"hovers": 1, "ms": 1000},
                "a_section": {"hovers": 1, "ms": 1000},
                "z_section": {"hovers": 1, "ms": 5000},
            },
        )
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    names = [item.section for item in overview.section_activity]
    assert names == ["z_section", "a_section", "b_section"]


def test_section_share_percent():
    metrics = [
        _metric(
            id=1,
            cursor_hover_data={
                "a": {"hovers": 1, "ms": 1000},
                "b": {"hovers": 1, "ms": 3000},
            },
        )
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    by_name = {item.section: item.share_percent for item in overview.section_activity}
    assert by_name["a"] == 25.0
    assert by_name["b"] == 75.0


def test_section_top_10_cap():
    hover_data = {f"section_{i}": {"hovers": 1, "ms": (i + 1) * 1000} for i in range(15)}
    metrics = [_metric(id=1, cursor_hover_data=hover_data)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert len(overview.section_activity) == 10
    assert overview.section_activity[0].section == "section_14"


def test_section_malformed_shapes_are_skipped_without_crashing():
    metrics = [
        _metric(id=1, cursor_hover_data="not-a-dict"),
        _metric(id=2, cursor_hover_data=None),
        _metric(
            id=3,
            cursor_hover_data={
                "bad_value": "not-a-dict",
                "": {"hovers": 1, "ms": 1000},
                "ok": {"hovers": 1, "ms": 500},
            },
        ),
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    names = [item.section for item in overview.section_activity]
    assert names == ["ok"]


def test_section_negative_and_non_finite_values_default_to_zero():
    metrics = [
        _metric(
            id=1,
            cursor_hover_data={
                "hero": {"hovers": -5, "ms": math.nan},
                "services": {"hovers": 2, "ms": 1000},
            },
        )
    ]
    overview = build_overview([], metrics, period="week", now=NOW)
    by_name = {item.section: item for item in overview.section_activity}
    # hero's hovers/ms are both invalid -> both default to 0 -> section
    # contributes nothing and is dropped entirely (nothing meaningful to show).
    assert "hero" not in by_name
    assert by_name["services"].interactions_count == 2


def test_section_unknown_section_names_are_preserved_verbatim():
    metrics = [_metric(id=1, cursor_hover_data={"some_new_section_v2": {"hovers": 1, "ms": 100}})]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.section_activity[0].section == "some_new_section_v2"


def test_section_no_data_no_division_by_zero():
    metrics = [_metric(id=1, cursor_hover_data={})]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.section_activity == ()


# --- multiple metrics per application ---------------------------------------


def test_applications_with_metrics_counts_unique_application_ids_not_rows():
    applications = [_app(1, IN_PERIOD), _app(2, IN_PERIOD)]
    metrics = [
        _metric(id=1, application_id=1, time_on_page=10),
        _metric(id=2, application_id=1, time_on_page=20),
        _metric(id=3, application_id=2, time_on_page=30),
    ]
    overview = build_overview(applications, metrics, period="week", now=NOW)
    assert overview.metrics_count == 3
    assert overview.applications_with_metrics == 2
    assert overview.applications_without_metrics == 0


def test_applications_without_metrics_excludes_apps_with_no_matching_metric():
    applications = [_app(1, IN_PERIOD), _app(2, IN_PERIOD)]
    metrics = [_metric(id=1, application_id=1, time_on_page=10)]
    overview = build_overview(applications, metrics, period="week", now=NOW)
    assert overview.applications_with_metrics == 1
    assert overview.applications_without_metrics == 1


def test_orphan_metric_without_matching_application_is_still_counted_via_own_timestamp():
    metrics = [_metric(id=1, application_id=999, created_at=IN_PERIOD, time_on_page=5)]
    overview = build_overview([], metrics, period="week", now=NOW)
    assert overview.metrics_count == 1
    assert overview.applications_with_metrics == 0


def test_metric_missing_created_at_falls_back_to_application_created_at():
    application = _app(1, created_at=IN_PERIOD)
    metric = _metric(id=1, application_id=1, created_at=None, time_on_page=5)
    overview = build_overview([application], [metric], period="day", now=NOW)
    assert overview.metrics_count == 1


def test_metric_missing_created_at_and_unmatched_application_is_safely_excluded():
    metric = _metric(id=1, application_id=999, created_at=None, time_on_page=5)
    overview = build_overview([], [metric], period="week", now=NOW)
    assert overview.metrics_count == 0


# --- application detail: multiple metrics -----------------------------------


def test_detail_no_metrics_returns_empty_shape():
    detail = build_application_detail(42, [])
    assert detail.has_metrics is False
    assert detail.application_id == 42
    assert detail.time_on_page_seconds is None
    assert detail.return_count is None
    assert detail.clicked_buttons == ()
    assert detail.section_activity == ()
    assert detail.total_button_clicks == 0
    assert detail.recorded_at is None


def test_detail_single_metric():
    metric = _metric(
        id=1,
        time_on_page=42,
        return_count=2,
        clicked_buttons=[{"button": "cta", "count": 3}],
        cursor_hover_data={"hero": {"hovers": 1, "ms": 1000}},
        created_at=NOW,
    )
    detail = build_application_detail(1, [metric])
    assert detail.has_metrics is True
    assert detail.time_on_page_seconds == 42.0
    assert detail.return_count == 2
    assert detail.total_button_clicks == 3
    assert detail.clicked_buttons[0].name == "cta"
    assert detail.section_activity[0].section == "hero"
    assert detail.recorded_at == NOW


def test_detail_multiple_metrics_are_summed_deterministically():
    earlier = NOW - timedelta(days=1)
    metrics = [
        _metric(
            id=1,
            time_on_page=10,
            return_count=1,
            clicked_buttons=[{"button": "cta", "count": 1}],
            created_at=earlier,
        ),
        _metric(
            id=2,
            time_on_page=20,
            return_count=2,
            clicked_buttons=[{"button": "cta", "count": 2}],
            created_at=NOW,
        ),
    ]
    detail = build_application_detail(1, metrics)
    assert detail.time_on_page_seconds == 30.0
    assert detail.return_count == 3
    assert detail.total_button_clicks == 3
    # recorded_at is deterministic: the most recent of the two rows, never
    # an arbitrary/random pick between them.
    assert detail.recorded_at == NOW


def test_detail_malformed_json_does_not_raise():
    metric = _metric(
        id=1,
        time_on_page=None,
        return_count=-5,
        clicked_buttons="not-a-list",
        cursor_hover_data=None,
        created_at=NOW,
    )
    detail = build_application_detail(1, [metric])
    assert detail.has_metrics is True
    assert detail.time_on_page_seconds is None
    assert detail.return_count is None
    assert detail.clicked_buttons == ()
    assert detail.section_activity == ()
