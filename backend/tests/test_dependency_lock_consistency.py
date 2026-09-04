"""Stage 3: lock-consistency regression for backend/requirements*.lock.txt.

These lock files (see their own header comments for the exact `pip-compile`
regeneration command) are the deterministic, hash-verified dependency set
backend/Dockerfile installs from (`pip install --require-hashes -r
requirements.lock.txt`) - see the Dockerfile's own comments for why. A full
re-resolve-and-diff against PyPI would need network access and would be slow
and flaky for an ordinary test run (this project's test suite never needs
network - see tests/conftest.py), so this instead checks the two properties
that are cheap to verify offline and catch the most common way a lock
silently drifts out of sync with pyproject.toml: someone adds/removes a
direct dependency (or a test-only one) and forgets to regenerate the lock.

Not a substitute for actually regenerating the lock when pyproject.toml
changes - see the header comment in each requirements*.lock.txt file for the
exact command to do that.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = BACKEND_DIR / "pyproject.toml"
MAIN_LOCK_PATH = BACKEND_DIR / "requirements.lock.txt"
DEV_LOCK_PATH = BACKEND_DIR / "requirements-dev.lock.txt"

# A requirement string's package name is everything before the first
# character that can start a version specifier/extras/marker clause -
# good enough for this project's own short, simple pyproject.toml
# dependency list (no URLs, no environment markers on direct deps).
_REQUIREMENT_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
# A pinned line in a pip-compile lock file, e.g. "alembic==1.18.5 \" or, for
# a dependency declared with extras in pyproject.toml (psycopg[binary],
# uvicorn[standard]), "psycopg[binary]==3.2.13 \" - the optional bracketed
# group below skips over that extras suffix so group(1) is always the bare
# package name, matching how _direct_dependency_names() parses pyproject.toml.
_LOCK_PIN_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==(\S+)", re.MULTILINE
)


def _normalize(name: str) -> str:
    # PEP 503 normalization - matches how pip/pip-compile itself compares
    # package names, so e.g. "pydantic-settings" and "pydantic_settings"
    # are recognized as the same package.
    return re.sub(r"[-_.]+", "-", name).lower()


def _direct_dependency_names(requirement_strings: list[str]) -> set[str]:
    names = set()
    for requirement in requirement_strings:
        match = _REQUIREMENT_NAME_RE.match(requirement.strip())
        assert match, f"could not parse a package name out of requirement {requirement!r}"
        names.add(_normalize(match.group(1)))
    return names


def _locked_package_names(lock_text: str) -> set[str]:
    return {_normalize(name) for name, _version in _LOCK_PIN_RE.findall(lock_text)}


def _pyproject_data() -> dict:
    with PYPROJECT_PATH.open("rb") as f:
        return tomllib.load(f)


def test_every_direct_dependency_is_pinned_in_the_main_lock():
    pyproject = _pyproject_data()
    direct_deps = _direct_dependency_names(pyproject["project"]["dependencies"])

    lock_text = MAIN_LOCK_PATH.read_text(encoding="utf-8")
    locked = _locked_package_names(lock_text)

    missing = direct_deps - locked
    assert not missing, (
        f"pyproject.toml declares {sorted(missing)} as direct dependencies, but "
        f"{MAIN_LOCK_PATH.name} does not pin them - regenerate the lock (see its own "
        "header comment for the exact pip-compile command)"
    )


def test_every_test_extra_dependency_is_pinned_in_the_dev_lock():
    pyproject = _pyproject_data()
    test_extra_deps = _direct_dependency_names(
        pyproject["project"]["optional-dependencies"]["test"]
    )

    lock_text = DEV_LOCK_PATH.read_text(encoding="utf-8")
    locked = _locked_package_names(lock_text)

    missing = test_extra_deps - locked
    assert not missing, (
        f"pyproject.toml's [test] extra declares {sorted(missing)}, but "
        f"{DEV_LOCK_PATH.name} does not pin them - regenerate the lock (see its own "
        "header comment for the exact pip-compile command)"
    )


def test_dev_lock_is_a_superset_of_the_main_lock_pins():
    # requirements-dev.lock.txt is generated with `--constraint
    # requirements.lock.txt` (see its header comment) precisely so shared
    # packages resolve to the exact same pinned version in both files - a
    # backend test importing a package pinned one way at runtime and another
    # way in CI/local dev would defeat the point of locking at all.
    main_lock_text = MAIN_LOCK_PATH.read_text(encoding="utf-8")
    dev_lock_text = DEV_LOCK_PATH.read_text(encoding="utf-8")

    main_pins = {_normalize(name): version for name, version in _LOCK_PIN_RE.findall(main_lock_text)}
    dev_pins = {_normalize(name): version for name, version in _LOCK_PIN_RE.findall(dev_lock_text)}

    mismatched = []
    for name, main_version in main_pins.items():
        dev_version = dev_pins.get(name)
        if dev_version is None:
            mismatched.append(f"{name}: pinned in main lock (=={main_version}) but absent from dev lock")
        elif dev_version != main_version:
            mismatched.append(f"{name}: main lock has =={main_version!r}, dev lock has =={dev_version!r}")

    assert not mismatched, "requirements-dev.lock.txt has drifted from requirements.lock.txt:\n" + "\n".join(
        mismatched
    )


def test_every_locked_package_has_at_least_one_hash():
    # `pip install --require-hashes` (backend/Dockerfile) refuses to run at
    # all if even one requirement in the file is missing hashes - this
    # catches that failure mode here, offline, rather than only at Docker
    # build time.
    for lock_path in (MAIN_LOCK_PATH, DEV_LOCK_PATH):
        lock_text = lock_path.read_text(encoding="utf-8")
        # Split into per-package blocks: each starts at a `name==version`
        # line and runs until the next one (or end of file). Reuses
        # _LOCK_PIN_RE (not a bare re.finditer here) so a package declared
        # with extras in pyproject.toml (psycopg[binary], uvicorn[standard])
        # - whose lock line is "psycopg[binary]==3.2.13 \" - is still
        # recognized as its own block start, not merged into whichever
        # package happens to precede it in the file.
        starts = [m.start() for m in _LOCK_PIN_RE.finditer(lock_text)]
        assert starts, f"{lock_path.name} has no pinned packages at all"
        starts.append(len(lock_text))
        for start, end in zip(starts, starts[1:]):
            block = lock_text[start:end]
            package_line = block.splitlines()[0]
            assert "--hash=" in block, f"{lock_path.name}: {package_line!r} has no --hash= entries"
