"""Operator CLI for one-time administrative bootstrap tasks.

Not exposed over HTTP and not importable from any route - the only way to
run this is with shell access to the backend container, e.g.:

    docker compose exec -it backend python -m app.cli bootstrap-admin

This is the sole remaining way to create the first Admin (see
app/routes/auth.py, which no longer exposes a public registration
endpoint): a public HTTP client can never win the race to create it,
because there is no HTTP path that creates it at all. Reuses the exact same
validation (AdminRegister), Argon2id hashing, and first-admin concurrency
protection (the Postgres advisory lock in
app.crud.admin.register_first_admin) as the rest of the application - none
of that is duplicated here.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.exceptions import ConflictError, DomainValidationError
from app.crud import admin as admin_crud
from app.schemas.auth import AdminRegister


def _prompt_username() -> str:
    return input("Username: ")


def _prompt_password() -> str:
    # getpass never echoes input and never touches shell history - unlike a
    # --password CLI flag (visible in `ps`/shell history) or an env var
    # (visible via `docker inspect`/the process environment), this is the
    # only input channel that leaves no trace outside this process. Mirrors
    # registry/create-user.sh's existing interactive-password convention.
    password = getpass.getpass("Password (input hidden): ")
    confirm = getpass.getpass("Confirm password (input hidden): ")
    if password != confirm:
        raise SystemExit("Passwords do not match.")
    return password


def bootstrap_admin(
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    read_username: Callable[[], str] = _prompt_username,
    read_password: Callable[[], str] = _prompt_password,
) -> int:
    """Create the first Admin directly through the domain layer.

    Returns a process exit code: 0 on success, non-zero on any failure
    (validation error, an admin already exists, or an unexpected DB/internal
    error - this never overwrites or coexists with an existing admin).
    Never prints the password or its hash under any circumstance, success or
    failure - including an unexpected exception from the DB layer, which is
    caught, rolled back, and reported only as a fixed generic message (see
    the `except Exception` branch below).

    session_factory/read_username/read_password are overridable only so
    this can be exercised in tests against a disposable test database
    without touching real input streams or the production DB - the real
    CLI entry point below always uses the defaults.
    """
    username = read_username()
    password = read_password()

    try:
        payload = AdminRegister(username=username, password=password)
    except ValidationError as exc:
        print(f"Bootstrap refused: invalid username/password ({exc.error_count()} error(s)).", file=sys.stderr)
        return 1

    db = session_factory()
    try:
        admin_crud.register_first_admin(db, payload.username, payload.password)
    except ConflictError:
        print("Bootstrap refused: an administrator already exists.", file=sys.stderr)
        return 1
    except DomainValidationError as exc:
        # DomainValidationError's message is always a fixed, credential-free
        # string (see app.crud.admin.register_first_admin) - safe to print.
        print(f"Bootstrap refused: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Anything else - a DB/connection failure, an unexpected SQLAlchemy
        # error, or any other unhandled exception raised while
        # register_first_admin() was running. This must never reach the
        # operator's terminal as a raw traceback: SQLAlchemy's own exception
        # rendering (e.g. a StatementError/DBAPIError repr) can include the
        # failed statement's bound parameters, which for the INSERT this CLI
        # performs would be the freshly generated Argon2 password hash.
        # Deliberately does not print, log, or interpolate the caught
        # exception's message/repr/args anywhere below - only this fixed,
        # credential-free string ever reaches stderr.
        try:
            db.rollback()
        except Exception:
            # Best-effort: if rollback itself fails (e.g. the connection is
            # already gone), swallow it rather than let a second, unhandled
            # exception escape and defeat the whole point of this branch.
            pass
        print(
            "Bootstrap failed due to an unexpected internal error. "
            "No administrator was created.",
            file=sys.stderr,
        )
        return 1
    finally:
        db.close()

    print(f"Administrator '{payload.username}' created successfully.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "bootstrap-admin",
        help="Create the first admin (interactive prompts; fails if one already exists).",
    )
    args = parser.parse_args(argv)

    if args.command == "bootstrap-admin":
        return bootstrap_admin()
    raise AssertionError(f"unreachable: unknown command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
