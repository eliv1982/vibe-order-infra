"""Stage 4: regression guard for docker-compose.yml's container hardening
and the one-shot DB-lifecycle HEALTHCHECK correction.

Runs `docker compose config --format json` (exactly the command section 23
of the Stage 4 spec already requires as a manual check) and asserts on the
*effective*, fully-merged service definitions it prints - not a text/regex
match against docker-compose.yml itself, which would only prove the text is
present, not that Compose actually applies it. Skipped outright if the
`docker` CLI isn't available (mirrors how the PostgreSQL integration tests
skip without TEST_DATABASE_URL - see tests/db_safety_guard.py) - this suite
never starts a container itself, only asks Compose to resolve config.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"

_DOCKER_AVAILABLE = shutil.which("docker") is not None

pytestmark = pytest.mark.skipif(
    not _DOCKER_AVAILABLE, reason="docker CLI is not available - skipping compose config checks"
)


@pytest.fixture(scope="module")
def compose_services() -> dict:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(_ENV_EXAMPLE),
            "-f",
            str(_REPO_ROOT / "docker-compose.yml"),
            "--profile",
            "admin",
            "config",
            "--format",
            "json",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"docker compose config failed: {result.stderr}"
    return json.loads(result.stdout)["services"]


# --- Stage 3 MINOR correction: one-shot services must not inherit a
# meaningless HTTP HEALTHCHECK they can never satisfy (they never listen on
# any port and exit for good after doing their one job) --------------------

_ONE_SHOT_SERVICES = ("db-roles-bootstrap", "db-migrate", "db-roles-finalize")


@pytest.mark.parametrize("service_name", _ONE_SHOT_SERVICES)
def test_one_shot_services_disable_the_inherited_healthcheck(compose_services, service_name):
    healthcheck = compose_services[service_name].get("healthcheck")
    assert healthcheck is not None, f"{service_name} lost its healthcheck override entirely"
    assert healthcheck.get("disable") is True, (
        f"{service_name} does not disable the inherited backend-image HEALTHCHECK - "
        "it would still be probed over HTTP despite never listening on any port"
    )


def test_backend_itself_keeps_its_real_healthcheck(compose_services):
    """Negative control: the one-shot fix above must not have accidentally
    disabled backend's own (real, meaningful) HEALTHCHECK too - they are
    four independent service blocks built from the same image."""
    healthcheck = compose_services["backend"]["healthcheck"]
    assert not healthcheck.get("disable")
    assert "python" in healthcheck["test"]
    assert "healthcheck.py" in healthcheck["test"]


# --- Container hardening -----------------------------------------------


def _security_opts(service: dict) -> list[str]:
    return service.get("security_opt") or []


_ALL_SERVICES_EXCEPT_PGADMIN = (
    "postgres",
    "registry",
    "nginx",
    "backend",
    "db-roles-bootstrap",
    "db-migrate",
    "db-roles-finalize",
)


@pytest.mark.parametrize("service_name", _ALL_SERVICES_EXCEPT_PGADMIN)
def test_every_service_disables_privilege_escalation(compose_services, service_name):
    assert "no-new-privileges:true" in _security_opts(compose_services[service_name])


def test_pgadmin_deliberately_does_not_get_no_new_privileges(compose_services):
    """Negative control: `no-new-privileges` was tried live on pgAdmin and
    reverted (see docker-compose.yml's comment on the pgadmin service) -
    its own entrypoint detects the restricted context and silently
    re-listens on port 8080 instead of 80, breaking the fixed
    `127.0.0.1:5050:80` port mapping. Guards against someone re-adding it
    without re-proving compatibility (e.g. after also updating the port
    mapping)."""
    assert "no-new-privileges:true" not in _security_opts(compose_services["pgadmin"])


_DEEPLY_HARDENED_PYTHON_SERVICES = ("backend", "db-roles-bootstrap", "db-migrate", "db-roles-finalize")


@pytest.mark.parametrize("service_name", _DEEPLY_HARDENED_PYTHON_SERVICES)
def test_backend_image_services_drop_all_capabilities_and_use_a_read_only_root(
    compose_services, service_name
):
    service = compose_services[service_name]
    assert service.get("cap_drop") == ["ALL"]
    assert service.get("read_only") is True
    assert "/tmp" in (service.get("tmpfs") or [])


def test_nginx_drops_all_capabilities_except_the_minimum_it_needs(compose_services):
    nginx = compose_services["nginx"]
    assert nginx.get("cap_drop") == ["ALL"]
    assert set(nginx.get("cap_add") or []) == {"NET_BIND_SERVICE", "SETUID", "SETGID", "CHOWN"}
    assert nginx.get("read_only") is True
    tmpfs = nginx.get("tmpfs") or []
    assert "/var/cache/nginx" in tmpfs
    assert "/var/run" in tmpfs
    assert "/tmp" in tmpfs


@pytest.mark.parametrize("service_name", ("postgres", "pgadmin", "registry"))
def test_postgres_pgadmin_registry_keep_only_the_universally_safe_flag(
    compose_services, service_name
):
    """These three explicitly did NOT get cap_drop/read_only in this stage
    (see docker-compose.yml's per-service comments: their entrypoints do
    root-level setup - chown'ing a mounted volume, initdb, ... - before
    dropping privileges, and that was never proven compatible with a
    read-only root / dropped capabilities here). This is a negative control
    guarding against someone quietly copy-pasting the deeper hardening onto
    one of them without the same live compatibility proof."""
    service = compose_services[service_name]
    assert service.get("cap_drop") is None
    assert service.get("read_only") is not True
