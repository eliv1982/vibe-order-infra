"""Unit tests for app.cli's argument parsing/dispatch only.

No database needed - these never call bootstrap_admin()'s real body, only
prove `main()` routes to it (or refuses to run at all) based on argv. See
tests/test_cli_bootstrap_admin.py for the PostgreSQL-backed integration
tests of bootstrap_admin() itself.
"""

import pytest

from app import cli


def test_main_requires_a_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])


def test_main_rejects_an_unknown_subcommand():
    with pytest.raises(SystemExit):
        cli.main(["not-a-real-command"])


def test_main_dispatches_bootstrap_admin(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "bootstrap_admin", lambda: calls.append(1) or 0)

    exit_code = cli.main(["bootstrap-admin"])

    assert exit_code == 0
    assert calls == [1]
