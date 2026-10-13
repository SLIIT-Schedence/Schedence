"""Operator scripts report database failures safely (mocked: no database is contacted)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import OperationalError

import scripts.bootstrap_superadmin as bootstrap_script
import scripts.seed_roles as seed_script

HOST = "ep-secret-endpoint-123456.c-7.us-east-2.aws.neon.tech"


def _timeout() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception(f"connection timeout expired host: '{HOST}', hostaddr: '3.18.239.121'"))


async def _raise_timeout(*args, **kwargs):
    raise _timeout()


def test_seed_script_hides_host_names_on_connection_timeout(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(seed_script, "_seed", _raise_timeout)
    code = seed_script.main(["--dry-run"])
    output = capsys.readouterr()
    assert code == 1
    assert HOST not in output.out + output.err and "3.18.239.121" not in output.out + output.err
    assert "connection timed out" in output.err and "Traceback" not in output.err


def test_seed_script_debug_flag_shows_the_real_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(seed_script, "_seed", _raise_timeout)
    with pytest.raises(OperationalError):
        seed_script.main(["--debug"])


def test_bootstrap_script_hides_host_names_on_connection_timeout(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(bootstrap_script, "_bootstrap", _raise_timeout)
    code = bootstrap_script.main(["--email", "admin@example.edu", "--institution-id", str(uuid.uuid4()), "--yes"])
    output = capsys.readouterr()
    assert code == 1
    assert HOST not in output.out + output.err
    assert "Nothing was committed" in output.err
