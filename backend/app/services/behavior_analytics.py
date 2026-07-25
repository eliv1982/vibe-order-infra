"""Deterministic, explainable behavior-metrics analytics for the admin panel.

This module is intentionally pure, mirroring app.services.application_scoring:

- no HTTP / FastAPI imports, no route-handler dependencies;
- no database access, no commits, no mutation of any input object;
- no reads of the current wall-clock time (`now` is always passed in);
- same input always produces the same output.

Confirmed data semantics this module relies on (see frontend/src/metrics/
behaviorMetrics.ts and its test file, and app/models/behavior_metric.py):

- `clicked_buttons` is a list of `{"button": <name>, "count": <clicks>}`
  objects — already per-visit aggregated counts, not raw click events.
- `cursor_hover_data` is a dict keyed by section name, with values
  `{"hovers": <interaction count>, "ms": <total hover duration in
  MILLISECONDS>}`. There are no coordinates anywhere in this structure, so
  nothing here may be presented as a coordinate heatmap — only per-section
  activity/duration.
- `time_on_page` is stored in SECONDS (frontend rounds
  `(Date.now() - pageLoadedAt) / 1000` before sending it).
- `time_on_page` and `return_count` are plain SQL `Integer` columns (not
  JSONB), so in practice they are always non-negative ints written through
  Pydantic-validated endpoints. The defensive filtering below (None/
  negative/NaN/Infinity) exists for hypothetical legacy/malformed rows and
  for objects constructed directly in unit tests, not because the normal
  write path can currently produce such values.
- `BehaviorMetric.application_id` is NOT NULL with a UNIQUE constraint (see
  app/models/behavior_metric.py), so in the real schema at most one metric
  row can ever exist per application, and every metric always references an
  existing application (ON DELETE CASCADE). The "multiple metrics per
  application" / "orphan metric" handling below exists purely as defensive,
  unit-tested generality in case that constraint is ever relaxed, or a
  metric is analyzed independently of the Application list that happens to
  have been fetched alongside it.

Aggregation here runs in Python over an already period-scoped result set
(the caller/CRUD layer is expected to pre-filter with a WHERE clause for
efficiency). At the data volumes this project targets that's sufficient;
a materially larger dataset would need DB-level aggregates (SUM/COUNT/
GROUP BY, or a JSONB aggregate function) instead of loading every row.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol

Period = Literal["day", "week", "month"]

_PERIOD_DURATIONS: dict[Period, timedelta] = {
    "day": timedelta(hours=24),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}

_TOP_N = 10
# Safety cap on button/section display names in the response — guards
# against a pathologically long legacy/malformed JSON value, not a normal
# code path (real names are short, human-authored labels).
_MAX_NAME_LENGTH = 200


class ApplicationLike(Protocol):
    """Structural shape needed from an Application (or compatible stand-in)."""

    id: int
    created_at: datetime | None


class BehaviorMetricLike(Protocol):
    """Structural shape needed from a BehaviorMetric (or compatible stand-in).

    Field types are `Any` on purpose: this module must safely handle
    malformed/legacy values (wrong types, negative numbers, NaN/Infinity)
    rather than assume the schema-validated shape always holds.
    """

    id: int
    application_id: int | None
    created_at: datetime | None
    time_on_page: Any
    clicked_buttons: Any
    cursor_hover_data: Any
    return_count: Any


@dataclass(frozen=True)
class ButtonStat:
    name: str
    count: int
    share_percent: float


@dataclass(frozen=True)
class SectionStat:
    section: str
    total_duration_seconds: float
    average_duration_seconds: float
    interactions_count: int
    share_percent: float


@dataclass(frozen=True)
class AnalyticsOverview:
    period: Period
    period_start: datetime
    period_end: datetime
    applications_count: int
    metrics_count: int
    applications_with_metrics: int
    applications_without_metrics: int
    average_time_on_page_seconds: float | None
    median_time_on_page_seconds: float | None
    average_return_count: float | None
    total_return_count: int
    total_button_clicks: int
    unique_clicked_buttons: int
    popular_buttons: tuple[ButtonStat, ...] = field(default_factory=tuple)
    section_activity: tuple[SectionStat, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ApplicationBehaviorDetail:
    application_id: int
    has_metrics: bool
    time_on_page_seconds: float | None
    return_count: int | None
    clicked_buttons: tuple[ButtonStat, ...]
    section_activity: tuple[SectionStat, ...]
    total_button_clicks: int
    recorded_at: datetime | None


# --- timestamp helpers ------------------------------------------------


def _to_utc(dt: datetime) -> datetime:
    """Normalize to a UTC-aware datetime.

    A naive datetime is deterministically treated as already-UTC (this is
    an assumption for comparison purposes only, not a claim about its true
    origin) so period-boundary comparisons never raise Python's "can't
    compare offset-naive and offset-aware datetimes" TypeError and never
    500. Mirrors the same convention already used for created_at sorting in
    app/routes/applications.py.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_period_bounds(period: Period, now: datetime) -> tuple[datetime, datetime]:
    """Return (period_start, period_end) for a rolling, half-open window.

    period_end is exactly `now` (normalized to UTC); period_start is
    `period_end` minus a fixed rolling duration (24h / 7d / 30d — not a
    calendar day/week/month). The window is half-open:
    period_start <= timestamp < period_end.
    """
    period_end = _to_utc(now)
    period_start = period_end - _PERIOD_DURATIONS[period]
    return period_start, period_end


def _in_period(ts: datetime | None, start: datetime, end: datetime) -> bool:
    if ts is None:
        return False
    ts_utc = _to_utc(ts)
    return start <= ts_utc < end


def _effective_metric_timestamp(
    metric: BehaviorMetricLike, application_by_id: dict[int, ApplicationLike]
) -> datetime | None:
    """The timestamp used to decide whether a metric falls in the period.

    Prefers the metric's own created_at (BehaviorMetric always has one in
    the real schema). Falls back to the linked Application's created_at
    only if the metric's own created_at is missing (a hypothetical/legacy
    row) — this fallback is never exercised by real data today, but is
    kept because the task requires it to be handled explicitly rather than
    assumed away. If neither is available, the metric's period membership
    cannot be safely determined and it is excluded (never crashes, never
    guesses).
    """
    created_at = metric.created_at
    if created_at is not None:
        return created_at
    application_id = metric.application_id
    if application_id is not None:
        application = application_by_id.get(application_id)
        if application is not None:
            return application.created_at
    return None


# --- numeric validation --------------------------------------------------


def _valid_nonneg_finite_number(value: Any) -> float | None:
    """Accept a finite, non-negative int/float; reject everything else
    (None, bool, strings, negative numbers, NaN, Infinity) by returning
    None instead of raising."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def _valid_nonneg_int(value: Any) -> int | None:
    """Like _valid_nonneg_finite_number, but only for values that represent
    a whole count (int, or a float with no fractional part)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and math.isfinite(value) and value >= 0 and value == int(value):
        return int(value)
    return None


def _round2(value: float) -> float:
    return round(value, 2)


# --- name normalization ---------------------------------------------------


def _clean_name(raw: Any) -> str | None:
    """Strip + length-cap a button/section name; drop non-strings and
    strings that are blank after stripping. Deliberately NOT casefolded —
    distinct-looking names are never silently merged without evidence
    they represent the same real event."""
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    return stripped[:_MAX_NAME_LENGTH]


# --- clicked_buttons aggregation ------------------------------------------


def _extract_button_counts(clicked_buttons: Any) -> dict[str, int]:
    """Parse one metric row's clicked_buttons into {name: count}.

    Tolerates the documented real shape (list of {"button", "count"}
    dicts) plus arbitrary malformed/legacy input: a non-list value, list
    items that aren't dicts, missing/blank/non-string names, and missing/
    negative/non-finite/fractional counts are all silently skipped rather
    than raising or fabricating a count.
    """
    counts: dict[str, int] = {}
    if not isinstance(clicked_buttons, list):
        return counts
    for item in clicked_buttons:
        if not isinstance(item, dict):
            continue
        name = _clean_name(item.get("button"))
        if name is None:
            continue
        count = _valid_nonneg_int(item.get("count"))
        if count is None:
            # Malformed/None/negative/bool/non-finite — not usable data.
            continue
        if count <= 0:
            # Zero is valid collector data, but represents no click and is
            # excluded from click analytics.
            continue
        counts[name] = counts.get(name, 0) + count
    return counts


def _aggregate_button_counts(metrics: Sequence[BehaviorMetricLike]) -> dict[str, int]:
    total: dict[str, int] = {}
    for metric in metrics:
        for name, count in _extract_button_counts(metric.clicked_buttons).items():
            total[name] = total.get(name, 0) + count
    return total


def _build_button_stats(counts: dict[str, int]) -> tuple[ButtonStat, ...]:
    total_clicks = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    top = ordered[:_TOP_N]
    return tuple(
        ButtonStat(
            name=name,
            count=count,
            share_percent=_round2((count / total_clicks) * 100) if total_clicks > 0 else 0.0,
        )
        for name, count in top
    )


# --- cursor_hover_data (section activity) aggregation ----------------------


def _extract_section_raw(cursor_hover_data: Any) -> dict[str, tuple[int, float]]:
    """Parse one metric row's cursor_hover_data into
    {section: (interactions_count, total_duration_ms)}.

    Tolerates a non-dict value, non-string/blank keys, non-dict values,
    and missing/negative/non-finite "hovers"/"ms" fields — each invalid
    field defaults to 0 rather than dropping the whole section, unless
    both fields are unusable/absent, in which case the section contributes
    nothing.
    """
    result: dict[str, tuple[int, float]] = {}
    if not isinstance(cursor_hover_data, dict):
        return result
    for key, value in cursor_hover_data.items():
        name = _clean_name(key)
        if name is None or not isinstance(value, dict):
            continue
        hovers = _valid_nonneg_int(value.get("hovers")) or 0
        ms = _valid_nonneg_finite_number(value.get("ms")) or 0.0
        if hovers == 0 and ms == 0.0:
            continue
        prev_hovers, prev_ms = result.get(name, (0, 0.0))
        result[name] = (prev_hovers + hovers, prev_ms + ms)
    return result


def _aggregate_sections(metrics: Sequence[BehaviorMetricLike]) -> dict[str, tuple[int, float]]:
    total: dict[str, tuple[int, float]] = {}
    for metric in metrics:
        for name, (hovers, ms) in _extract_section_raw(metric.cursor_hover_data).items():
            prev_hovers, prev_ms = total.get(name, (0, 0.0))
            total[name] = (prev_hovers + hovers, prev_ms + ms)
    return total


def _build_section_stats(raw: dict[str, tuple[int, float]]) -> tuple[SectionStat, ...]:
    total_duration_all_seconds = sum(ms for _, ms in raw.values()) / 1000.0
    items = [(name, hovers, ms / 1000.0) for name, (hovers, ms) in raw.items()]
    items.sort(key=lambda t: (-t[2], t[0]))
    top = items[:_TOP_N]

    stats = []
    for name, hovers, total_duration_seconds in top:
        average_duration = (
            _round2(total_duration_seconds / hovers) if hovers > 0 else 0.0
        )
        share = (
            _round2((total_duration_seconds / total_duration_all_seconds) * 100)
            if total_duration_all_seconds > 0
            else 0.0
        )
        stats.append(
            SectionStat(
                section=name,
                total_duration_seconds=_round2(total_duration_seconds),
                average_duration_seconds=average_duration,
                interactions_count=hovers,
                share_percent=share,
            )
        )
    return tuple(stats)


# --- overview --------------------------------------------------------------


def build_overview(
    applications: Sequence[ApplicationLike],
    metrics: Sequence[BehaviorMetricLike],
    *,
    period: Period,
    now: datetime,
) -> AnalyticsOverview:
    """Build the full analytics overview for a rolling period.

    `applications` and `metrics` are expected to already be pre-filtered by
    the caller's DB query (created_at within/near the period) for
    efficiency, but this function re-validates the exact half-open boundary
    itself so period-boundary behavior is fully unit-testable without a
    database. Applications and metrics are filtered independently, each by
    their own created_at — a metric's period membership is never inferred
    from whether its application also falls in the period, and vice versa.
    """
    period_start, period_end = compute_period_bounds(period, now)

    applications_in_period = [
        application
        for application in applications
        if _in_period(application.created_at, period_start, period_end)
    ]
    # Full (not period-filtered) map: only used as a last-resort timestamp
    # fallback for a metric missing its own created_at (see
    # _effective_metric_timestamp) — it must be able to find an application
    # outside the period too, since that's exactly the case it's guarding.
    application_by_id = {application.id: application for application in applications}

    metrics_in_period = [
        metric
        for metric in metrics
        if _in_period(_effective_metric_timestamp(metric, application_by_id), period_start, period_end)
    ]

    applications_count = len(applications_in_period)
    metrics_count = len(metrics_in_period)

    application_ids_in_period = {application.id for application in applications_in_period}
    application_ids_with_metrics_in_period = {
        metric.application_id for metric in metrics_in_period if metric.application_id is not None
    }
    applications_with_metrics = len(
        application_ids_in_period & application_ids_with_metrics_in_period
    )
    applications_without_metrics = applications_count - applications_with_metrics

    time_values = [
        value
        for value in (_valid_nonneg_finite_number(metric.time_on_page) for metric in metrics_in_period)
        if value is not None
    ]
    average_time = _round2(statistics.mean(time_values)) if time_values else None
    median_time = _round2(statistics.median(time_values)) if time_values else None

    return_values = [
        value
        for value in (_valid_nonneg_int(metric.return_count) for metric in metrics_in_period)
        if value is not None
    ]
    average_return = _round2(statistics.mean(return_values)) if return_values else None
    total_return = sum(return_values) if return_values else 0

    button_counts = _aggregate_button_counts(metrics_in_period)
    total_clicks = sum(button_counts.values())
    popular_buttons = _build_button_stats(button_counts)

    section_raw = _aggregate_sections(metrics_in_period)
    section_activity = _build_section_stats(section_raw)

    return AnalyticsOverview(
        period=period,
        period_start=period_start,
        period_end=period_end,
        applications_count=applications_count,
        metrics_count=metrics_count,
        applications_with_metrics=applications_with_metrics,
        applications_without_metrics=applications_without_metrics,
        average_time_on_page_seconds=average_time,
        median_time_on_page_seconds=median_time,
        average_return_count=average_return,
        total_return_count=total_return,
        total_button_clicks=total_clicks,
        unique_clicked_buttons=len(button_counts),
        popular_buttons=popular_buttons,
        section_activity=section_activity,
    )


# --- single-application detail ----------------------------------------------


def build_application_detail(
    application_id: int, metrics: Sequence[BehaviorMetricLike]
) -> ApplicationBehaviorDetail:
    """Aggregate every metric row linked to one application.

    In the real schema `metrics` has at most one element (application_id is
    UNIQUE), but this aggregates deterministically over any number of rows
    so the behavior is well-defined and unit-tested even if that constraint
    is ever relaxed: time_on_page and return_count are summed across rows
    (each row represents one distinct recorded visit), clicked_buttons/
    section_activity are combined the same way as the overview, and
    recorded_at is the most recent of the rows' created_at values.
    """
    if not metrics:
        return ApplicationBehaviorDetail(
            application_id=application_id,
            has_metrics=False,
            time_on_page_seconds=None,
            return_count=None,
            clicked_buttons=(),
            section_activity=(),
            total_button_clicks=0,
            recorded_at=None,
        )

    time_values = [
        value
        for value in (_valid_nonneg_finite_number(metric.time_on_page) for metric in metrics)
        if value is not None
    ]
    time_on_page_seconds = _round2(sum(time_values)) if time_values else None

    return_values = [
        value for value in (_valid_nonneg_int(metric.return_count) for metric in metrics) if value is not None
    ]
    return_count = sum(return_values) if return_values else None

    button_counts = _aggregate_button_counts(metrics)
    total_clicks = sum(button_counts.values())
    clicked_buttons = _build_button_stats(button_counts)

    section_raw = _aggregate_sections(metrics)
    section_activity = _build_section_stats(section_raw)

    recorded_timestamps = [metric.created_at for metric in metrics if metric.created_at is not None]
    recorded_at = max(recorded_timestamps) if recorded_timestamps else None

    return ApplicationBehaviorDetail(
        application_id=application_id,
        has_metrics=True,
        time_on_page_seconds=time_on_page_seconds,
        return_count=return_count,
        clicked_buttons=clicked_buttons,
        section_activity=section_activity,
        total_button_clicks=total_clicks,
        recorded_at=recorded_at,
    )
