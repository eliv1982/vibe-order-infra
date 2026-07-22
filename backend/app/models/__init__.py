"""Import all models here so Base.metadata sees them before create_all() runs."""

from app.models.admin_setting import AdminSetting
from app.models.application import Application
from app.models.behavior_metric import BehaviorMetric

__all__ = ["Application", "BehaviorMetric", "AdminSetting"]
