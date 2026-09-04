"""Application settings loaded from environment variables (see docker-compose.yml)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# Known example/placeholder JWT secrets that must never be used for real - if
# JWT_SECRET_KEY is accidentally left at one of these (e.g. .env.example was
# copied to .env without editing it), Settings() must fail loudly instead of
# quietly starting up with a public, guessable secret.
_INSECURE_JWT_SECRET_PLACEHOLDERS = {
    "change_me_to_a_random_64_char_secret_generated_locally_1234567890",
    "changeme",
    "change_me",
    "secret",
    "your-secret-key",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    api_prefix: str = "/api"

    # Runtime application DB credential ONLY (Stage 2: database lifecycle /
    # least-privileged runtime). This is deliberately NOT the migration/owner
    # credential (see backend/alembic/env.py, which reads MIGRATION_DB_USER/
    # MIGRATION_DB_PASSWORD directly from the environment, never through this
    # Settings class) and NOT the Postgres cluster bootstrap/superuser
    # credential (POSTGRES_USER/POSTGRES_PASSWORD on the `postgres` container
    # itself - see docker-compose.yml and app/db_admin/). The backend process
    # must be able to run its normal CRUD operations with this credential
    # alone and nothing more - it has no DDL rights (see
    # app/db_admin/bootstrap_roles.py for the exact grants).
    app_db_user: str
    app_db_password: str
    postgres_db: str
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    # No default: absence must fail startup rather than silently run
    # unauthenticated-equivalent (a guessable/shared secret). min_length=32
    # rules out trivially weak values in addition to the placeholder check
    # below.
    jwt_secret_key: str = Field(..., min_length=32)
    # Restricted to symmetric HMAC variants only - this app only ever holds a
    # single shared secret, so "none" or an asymmetric algorithm would be a
    # misconfiguration, not a valid alternative.
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(30, gt=0, le=1440)

    @field_validator("jwt_secret_key")
    @classmethod
    def _reject_placeholder_jwt_secret(cls, value: str) -> str:
        if value.strip().lower() in _INSECURE_JWT_SECRET_PLACEHOLDERS:
            raise ValueError(
                "JWT_SECRET_KEY is set to a known placeholder value - generate a real "
                'secret (e.g. `python -c "import secrets; print(secrets.token_urlsafe(64))"`) '
                "and set it in your local .env"
            )
        return value

    @property
    def database_url(self) -> URL:
        # Built via URL.create() rather than an f-string so a password
        # containing reserved URL characters (@, :, /, %) is escaped
        # correctly instead of breaking the connection string. Always the
        # least-privileged runtime credential - see app_db_user/app_db_password
        # above. Alembic migrations use a completely separate URL (see
        # backend/alembic/env.py) and never this one.
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.app_db_user,
            password=self.app_db_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
