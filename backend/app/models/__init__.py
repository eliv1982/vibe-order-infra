"""Import all models here so Base.metadata sees every mapped class.

Stage 2: the only real schema-evolution mechanism is Alembic (see
backend/alembic/, whose env.py imports this package for exactly this
reason, before autogenerate/`alembic check` compares Base.metadata against
the live database). Base.metadata.create_all()/drop_all() are still used,
but only by the test suite against a disposable TEST_DATABASE_URL (see
tests/conftest.py) - never against a real/production database at runtime.
"""

from app.models.admin import Admin
from app.models.admin_setting import AdminSetting
from app.models.application import Application
from app.models.application_behavior_capability import ApplicationBehaviorCapability
from app.models.application_idempotency_key import ApplicationIdempotencyKey
from app.models.behavior_metric import BehaviorMetric

__all__ = [
    "Admin",
    "Application",
    "ApplicationBehaviorCapability",
    "ApplicationIdempotencyKey",
    "BehaviorMetric",
    "AdminSetting",
]
