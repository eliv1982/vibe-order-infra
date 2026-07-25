"""HTTP routes for protected admin behavior-metrics analytics.

Read-only: validates request params via schemas, delegates every DB read to
crud, and delegates all aggregation to the pure app.services.behavior_analytics
module. `now` is captured exactly once per request and passed explicitly into
the pure service, matching its "no hidden clock" contract.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.crud import application as application_crud
from app.crud import behavior_metric as behavior_metric_crud
from app.schemas.analytics import (
    AnalyticsOverview,
    ApplicationBehaviorAnalyticsRead,
    ButtonAnalyticsItem,
    Period,
    SectionAnalyticsItem,
)
from app.services import behavior_analytics as analytics_service

router = APIRouter(
    prefix="/analytics", tags=["analytics"], dependencies=[Depends(get_current_admin)]
)


def _to_button_item(stat: analytics_service.ButtonStat) -> ButtonAnalyticsItem:
    return ButtonAnalyticsItem(name=stat.name, count=stat.count, share_percent=stat.share_percent)


def _to_section_item(stat: analytics_service.SectionStat) -> SectionAnalyticsItem:
    return SectionAnalyticsItem(
        section=stat.section,
        total_duration_seconds=stat.total_duration_seconds,
        average_duration_seconds=stat.average_duration_seconds,
        interactions_count=stat.interactions_count,
        share_percent=stat.share_percent,
    )


@router.get("/overview", response_model=AnalyticsOverview)
def get_analytics_overview(
    period: Period = Query("week"), db: Session = Depends(get_db)
) -> AnalyticsOverview:
    now = datetime.now(timezone.utc)
    period_start, period_end = analytics_service.compute_period_bounds(period, now)

    applications = application_crud.get_applications_created_between(db, period_start, period_end)
    metrics = behavior_metric_crud.get_behavior_metrics_created_between(db, period_start, period_end)

    overview = analytics_service.build_overview(applications, metrics, period=period, now=now)

    return AnalyticsOverview(
        period=overview.period,
        period_start=overview.period_start,
        period_end=overview.period_end,
        applications_count=overview.applications_count,
        metrics_count=overview.metrics_count,
        applications_with_metrics=overview.applications_with_metrics,
        applications_without_metrics=overview.applications_without_metrics,
        average_time_on_page_seconds=overview.average_time_on_page_seconds,
        median_time_on_page_seconds=overview.median_time_on_page_seconds,
        average_return_count=overview.average_return_count,
        total_return_count=overview.total_return_count,
        total_button_clicks=overview.total_button_clicks,
        unique_clicked_buttons=overview.unique_clicked_buttons,
        popular_buttons=[_to_button_item(stat) for stat in overview.popular_buttons],
        section_activity=[_to_section_item(stat) for stat in overview.section_activity],
    )


@router.get("/applications/{application_id}", response_model=ApplicationBehaviorAnalyticsRead)
def get_application_behavior_analytics(
    application_id: int, db: Session = Depends(get_db)
) -> ApplicationBehaviorAnalyticsRead:
    application = application_crud.get_application(db, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    metrics = behavior_metric_crud.get_behavior_metrics_by_application(db, application_id)
    detail = analytics_service.build_application_detail(application_id, metrics)

    return ApplicationBehaviorAnalyticsRead(
        application_id=detail.application_id,
        has_metrics=detail.has_metrics,
        time_on_page_seconds=detail.time_on_page_seconds,
        return_count=detail.return_count,
        clicked_buttons=[_to_button_item(stat) for stat in detail.clicked_buttons],
        section_activity=[_to_section_item(stat) for stat in detail.section_activity],
        total_button_clicks=detail.total_button_clicks,
        recorded_at=detail.recorded_at,
    )
