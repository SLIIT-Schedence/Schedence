"""scripts/run_services.py: service table, commands and orchestration, with fake processes (no servers start)."""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("run_services", ROOT / "scripts" / "run_services.py")
rs = importlib.util.module_from_spec(SPEC)
sys.modules["run_services"] = rs  # dataclasses resolve postponed annotations through sys.modules
SPEC.loader.exec_module(rs)

LOOP = ["--loop", "asyncio:SelectorEventLoop"] if os.name == "nt" else []


def service(key):
    return next(item for item in rs.SERVICES if item.key == key)


def test_services_and_default_ports():
    assert [(item.key, item.default_port) for item in rs.SERVICES] == [
        ("identity", 8001), ("academic", 8002), ("examination", 8003), ("teaching", 8004)]
    assert not any(item.key == "resource" for item in rs.SERVICES)  # Models and migrations only; no HTTP runtime.


@pytest.mark.parametrize("key,port", [("identity", 8001), ("academic", 8002), ("examination", 8003)])
def test_existing_service_commands_are_unchanged(key, port):
    base = [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "127.0.0.1", "--port", str(port)]
    assert rs.uvicorn_command(service(key), port, host="127.0.0.1", reload=False) == base + LOOP
    assert rs.uvicorn_command(service(key), port, host="127.0.0.1", reload=True) == base + LOOP + [
        "--reload", "--reload-dir", "app", "--reload-dir", str(rs.ROOT / "packages")]
    assert service(key).db_health


def test_teaching_uses_its_real_entry_point_and_directory():
    teaching = service("teaching")
    assert teaching.directory == ROOT / "services" / "teaching-service"
    assert (teaching.directory / "src" / "schedence_teaching" / "main.py").is_file()
    assert rs.uvicorn_command(teaching, 8004, host="127.0.0.1", reload=True) == [
        sys.executable, "-m", "uvicorn", "schedence_teaching.main:create_app", "--factory", "--host", "127.0.0.1", "--port", "8004",
        "--app-dir", "src", *LOOP, "--reload", "--reload-dir", "src", "--reload-dir", str(rs.ROOT / "packages")]
    assert not teaching.db_health  # /health only; never reported as a healthy database check.


def test_teaching_factory_is_the_real_application_with_health():
    sys.path.insert(0, str(service("teaching").directory / "src"))
    from fastapi.testclient import TestClient
    from schedence_teaching.main import create_app
    with TestClient(create_app()) as client:
        assert client.get("/health").json() == {"status": "ok", "service": "teaching-timetable"}


class FakeProcess:
    pid, returncode = 0, 0

    def poll(self):
        return 0


class FakeRunning:
    def __init__(self, item, port):
        self.service, self.port, self.process, self.recent = item, port, FakeProcess(), []

    @property
    def alive(self):
        return False


@pytest.fixture
def launcher(monkeypatch):
    calls = dict(started=[], overrides={}, db_checked=[], stopped=0)
    monkeypatch.setattr(rs, "port_in_use", lambda port: False)
    monkeypatch.setattr(rs, "check_database", lambda services: pytest.fail("database check must be skipped"))

    def fake_start(item, port, *, host, reload, env_overrides):
        calls["started"].append((item.key, port))
        calls["overrides"][item.key] = dict(env_overrides)
        return FakeRunning(item, port)
    monkeypatch.setattr(rs, "start", fake_start)
    monkeypatch.setattr(rs, "wait_until_healthy", lambda running: True)

    class Response:
        def json(self):
            return {"status": "healthy"}

    def fake_get(url, timeout):
        calls["db_checked"].append(url)
        return Response()
    monkeypatch.setattr(rs.httpx, "get", fake_get)
    original_stop = rs.stop_all

    def fake_stop(processes, **kwargs):
        calls["stopped"] += 1
        original_stop(processes, **kwargs)
    monkeypatch.setattr(rs, "stop_all", fake_stop)
    cwd = os.getcwd()
    yield calls
    os.chdir(cwd)


def test_only_teaching_starts_just_teaching_without_a_database_health_check(launcher):
    assert rs.main(["--only", "teaching", "--skip-db-check", "--smoke"]) == 0
    assert launcher["started"] == [("teaching", 8004)] and launcher["db_checked"] == []
    assert launcher["overrides"]["teaching"] == {}  # No Identity override without Identity; no preview mode.


def test_all_services_start_and_teaching_learns_the_identity_url(launcher):
    assert rs.main(["--skip-db-check", "--smoke"]) == 0
    assert [key for key, _ in launcher["started"]] == ["identity", "academic", "examination", "teaching"]
    assert launcher["overrides"]["teaching"]["IDENTITY_SERVICE_URL"] == "http://127.0.0.1:8001"
    assert len(launcher["db_checked"]) == 3 and not any(":8004/" in url for url in launcher["db_checked"])


def test_local_preview_is_explicit_and_teaching_only(launcher, tmp_path):
    manifest = tmp_path / "manifest.json"
    assert rs.main(["--only", "teaching", "--skip-db-check", "--smoke", "--teaching-local-preview", str(manifest)]) == 0
    assert launcher["overrides"]["teaching"] == {"TEACHING_READ_STORE": "LOCAL_PREVIEW", "TEACHING_LOCAL_PREVIEW_MANIFEST": str(manifest.resolve())}
    launcher["started"].clear()
    assert rs.main(["--only", "identity", "--skip-db-check", "--smoke", "--teaching-local-preview", str(manifest)]) == 1
    assert launcher["started"] == []  # Refused before anything starts.


def test_a_service_that_does_not_become_healthy_stops_everything(launcher, monkeypatch):
    monkeypatch.setattr(rs, "wait_until_healthy", lambda running: running.service.key != "teaching")
    assert rs.main(["--only", "academic", "--only", "teaching", "--skip-db-check"]) == 1
    assert launcher["stopped"] == 1


def test_busy_teaching_port_is_refused(monkeypatch):
    monkeypatch.setattr(rs, "port_in_use", lambda port: port == 8004)
    cwd = os.getcwd()
    try:
        assert rs.main(["--only", "teaching", "--skip-db-check"]) == 1
    finally:
        os.chdir(cwd)
