from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path

import pytest

from support import REMOTE_URL

from schedence_db.config import ENV_FILE_VARIABLE

_SETTINGS_VARIABLES = ("APP_ENV", "DATABASE_URL", "DIRECT_DATABASE_URL", ENV_FILE_VARIABLE)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Also run tests marked 'live' against the real configured database.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live") or os.environ.get("SCHEDENCE_DB_LIVE_TESTS") == "1":
        return
    skip_live = pytest.mark.skip(reason="live database test; run with --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


def pytest_asyncio_loop_factories(config: pytest.Config, item: pytest.Item) -> dict[str, object]:
    # psycopg async cannot run on Windows' default ProactorEventLoop.
    return {"selector": asyncio.SelectorEventLoop}


@pytest.fixture(autouse=True)
def isolated_environment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Unit tests never see the developer's real database settings or .env file."""
    if request.node.get_closest_marker("live"):
        return
    for name in list(os.environ):
        upper = name.upper()
        if upper in _SETTINGS_VARIABLES or upper.startswith("DB_") or upper.endswith("_DATABASE_URL"):
            monkeypatch.delenv(name)
    # A .git marker bounds the upward .env search to tmp_path.
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def remote_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", REMOTE_URL)


@pytest.fixture
def closed_port() -> int:
    """A local TCP port with nothing listening on it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
