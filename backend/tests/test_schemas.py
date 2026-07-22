"""Schema-level validation tests.

No database or network access required — these always run, including when
TEST_DATABASE_URL is not set.
"""

import pytest
from pydantic import ValidationError

from app.schemas.admin_setting import AdminSettingCreate, AdminSettingUpdate
from app.schemas.application import ApplicationUpdate
from app.schemas.behavior_metric import BehaviorMetricCreate, BehaviorMetricUpdate


def test_admin_setting_create_rejects_budget_min_above_max():
    with pytest.raises(ValidationError):
        AdminSettingCreate(service_name="Consulting", budget_min=500, budget_max=100)


def test_admin_setting_create_accepts_valid_budget_range():
    setting = AdminSettingCreate(service_name="Consulting", budget_min=100, budget_max=500)
    assert setting.budget_min <= setting.budget_max


def test_admin_setting_create_rejects_negative_budget_min():
    with pytest.raises(ValidationError):
        AdminSettingCreate(service_name="Consulting", budget_min=-10, budget_max=100)


def test_admin_setting_create_rejects_negative_budget_max():
    with pytest.raises(ValidationError):
        AdminSettingCreate(service_name="Consulting", budget_min=0, budget_max=-100)


def test_admin_setting_update_rejects_budget_min_above_max_when_both_given():
    with pytest.raises(ValidationError):
        AdminSettingUpdate(budget_min=700, budget_max=500)


def test_admin_setting_update_allows_single_field_regardless_of_stored_state():
    # The schema alone can't know the persisted budget_max, so a lone
    # budget_min is accepted here; the merged-state rule is enforced in
    # crud.update_admin_setting (see tests/test_api.py).
    update = AdminSettingUpdate(budget_min=700)
    assert update.model_fields_set == {"budget_min"}


def test_admin_setting_update_rejects_explicit_null_for_service_name():
    with pytest.raises(ValidationError):
        AdminSettingUpdate(service_name=None)


def test_admin_setting_update_rejects_explicit_null_for_is_active():
    with pytest.raises(ValidationError):
        AdminSettingUpdate(is_active=None)


def test_admin_setting_update_allows_explicit_null_for_description():
    update = AdminSettingUpdate(description=None)
    assert "description" in update.model_fields_set
    assert update.description is None


def test_application_update_rejects_explicit_null_for_not_null_field():
    with pytest.raises(ValidationError):
        ApplicationUpdate(first_name=None)


def test_application_update_allows_explicit_null_for_nullable_fields():
    update = ApplicationUpdate(middle_name=None, comment=None)
    assert update.middle_name is None
    assert update.comment is None
    assert {"middle_name", "comment"} <= update.model_fields_set


def test_application_update_allows_omitting_fields():
    update = ApplicationUpdate(first_name="Ivan")
    assert update.model_fields_set == {"first_name"}


def test_behavior_metric_update_rejects_explicit_null_for_not_null_field():
    with pytest.raises(ValidationError):
        BehaviorMetricUpdate(time_on_page=None)


def test_behavior_metric_update_allows_omitting_fields():
    update = BehaviorMetricUpdate(return_count=3)
    assert update.model_fields_set == {"return_count"}


def test_behavior_metric_create_rejects_non_positive_application_id():
    with pytest.raises(ValidationError):
        BehaviorMetricCreate(application_id=0)
    with pytest.raises(ValidationError):
        BehaviorMetricCreate(application_id=-5)


def test_behavior_metric_create_accepts_positive_application_id():
    metric = BehaviorMetricCreate(application_id=1)
    assert metric.application_id == 1
