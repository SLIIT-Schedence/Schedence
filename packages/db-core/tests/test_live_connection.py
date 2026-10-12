"""Opt-in check against the real configured database (e.g. Neon).

Skipped unless requested:  python -m pytest --live
Runs only SELECT 1: reads nothing, writes nothing, and prints no credentials.
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import NullPool

from schedence_db.config import load_settings
from schedence_db.connection import create_db_engine, dispose_engine
from schedence_db.health import check_database

pytestmark = pytest.mark.live


@pytest.mark.parametrize("direct", [False, True], ids=["pooled", "direct"])
async def test_live_select_one(direct: bool) -> None:
    settings = load_settings()
    if direct and not settings.has_direct_url:
        pytest.skip("DIRECT_DATABASE_URL is not configured")

    engine = create_db_engine(settings, direct=direct, poolclass=NullPool)
    try:
        health = await check_database(engine, timeout=30)
    finally:
        await dispose_engine(engine)

    assert health.healthy, f"live database check failed: {health.error}"
