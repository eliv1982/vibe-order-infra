"""Import all models here so Base.metadata sees them before create_all() runs."""

from app.models.admin import Admin
from app.models.admin_setting import AdminSetting
from app.models.application import Application
from app.models.application_behavior_capability import ApplicationBehaviorCapability
from app.models.behavior_metric import BehaviorMetric

__all__ = [
    "Admin",
    "Application",
    "ApplicationBehaviorCapability",
    "BehaviorMetric",
    "AdminSetting",
]
