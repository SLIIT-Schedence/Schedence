from __future__ import annotations

from pathlib import Path

import pytest
from support import FAKE_USER, REMOTE_URL, assert_no_secrets, local_url

from schedence_db import check_connection
from schedence_db.health import DatabaseHealth


def run_cli(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = check_connection.main(list(args))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_reports_healthy_database(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, remote_env: None
) -> None:
    async def fake_probe(settings, *, direct, timeout):  # type: ignore[no-untyped-def]
        return DatabaseHealth(healthy=True, latency_ms=42.0), (17, 5)

    monkeypatch.setattr(check_connection, "_probe", fake_probe)

    code, output = run_cli(capsys)

    assert code == 0
    for line in (
        "Schedence Database Connection Test",
        "Configuration: Loaded",
        "Endpoint: pooled (DATABASE_URL)",
        "Database: PostgreSQL 17.5",
        "Connection: Successful",
        "Query: SELECT 1",
        "Status: Healthy",
    ):
        assert line in output
    assert_no_secrets(output)
    assert FAKE_USER not in output
    assert "neon.tech" not in output


def test_reports_unreachable_database(capsys: pytest.CaptureFixture[str], closed_port: int, tmp_path: Path) -> None:
    env_file = tmp_path / "local.env"
    env_file.write_text(f"DATABASE_URL={local_url(closed_port)}\nDB_CONNECT_TIMEOUT=3\n", encoding="utf-8")

    code, output = run_cli(capsys, "--env-file", str(env_file), "--timeout", "10")

    assert code == 1
    assert "Connection: Failed" in output
    assert "Status: Unhealthy" in output
    assert_no_secrets(output)
    assert FAKE_USER not in output


def test_configuration_error_exit_code_and_hint(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"DATABASE_URL:{REMOTE_URL}", encoding="utf-8")

    code, output = run_cli(capsys, "--env-file", str(env_file))

    assert code == 2
    assert "Configuration: Failed" in output
    assert "DATABASE_URL is required" in output
    assert "equals sign" in output
    assert_no_secrets(output)


def test_direct_flag_requires_direct_url(capsys: pytest.CaptureFixture[str], remote_env: None) -> None:
    code, output = run_cli(capsys, "--direct")

    assert code == 2
    assert "DIRECT_DATABASE_URL is not set" in output
