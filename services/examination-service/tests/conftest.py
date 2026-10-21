from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from schedence_db.config import ENV_FILE_VARIABLE

SERVICE_DIR = Path(__file__).resolve().parents[1]
_SETTINGS_VARIABLES = ("APP_ENV", "DATABASE_URL", "DIRECT_DATABASE_URL", "FRONTEND_URL", ENV_FILE_VARIABLE)
_SETTINGS_PREFIXES = ("DB_", "AWS_", "COGNITO_", "IDENTITY_", "SLIIT_", "EXAMINATION_", "M3_DEMO_")


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
        if upper in _SETTINGS_VARIABLES or upper.startswith(_SETTINGS_PREFIXES) or upper.endswith("_DATABASE_URL"):
            monkeypatch.delenv(name)
    (tmp_path / ".git").mkdir()  # bounds the upward .env search to tmp_path
    monkeypatch.chdir(tmp_path)
