"""Opt-in, read-only checks against the real configured database.

Skipped unless requested:  python -m pytest --live
Every query runs in a READ ONLY transaction that is rolled back, so the
shared database cannot change.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from schedence_db import create_db_engine, dispose_engine, load_settings

from app.auth.dependencies import require_exam_data_access
from app.database import close_database, open_database
from app.main import create_app
from app.seed.generator import generate_dataset
from app.sources import DatabaseSource, DatasetSource

pytestmark = pytest.mark.live


async def test_database_source_matches_the_generated_dataset() -> None:
    engine = create_db_engine(load_settings(), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                db = DatabaseSource(AsyncSession(bind=connection))
                memory = DatasetSource(generate_dataset())

                assert await db.list_modules() == await memory.list_modules()
                assert await db.list_modules(specialization="CYBER") == await memory.list_modules(
                    specialization="CYBER"
                )
                assert await db.get_module("IT3041") == await memory.get_module("IT3041")
                assert await db.list_students() == await memory.list_students()
                assert await db.list_students(academic_year=2, limit=7, offset=3) == await memory.list_students(
                    academic_year=2, limit=7, offset=3
                )
                assert await db.list_registrations() == await memory.list_registrations()
                assert await db.list_registrations(registration_type="repeat") == await memory.list_registrations(
                    registration_type="repeat"
                )
                assert await db.list_venues() == await memory.list_venues()
                assert await db.list_venue_blocks() == await memory.list_venue_blocks()
                assert await db.list_exam_periods() == await memory.list_exam_periods()
            finally:
                await transaction.rollback()
    finally:
        await dispose_engine(engine)


async def test_api_sessions_are_read_only_on_the_real_database() -> None:
    app = FastAPI()
    await open_database(app)
    try:
        async with app.state.session_factory() as session:
            assert (await session.execute(text("SHOW transaction_read_only"))).scalar_one() == "on"
            await session.rollback()
    finally:
        await close_database(app)


def test_live_api_end_to_end() -> None:
    # Database check only: the caller is treated as an examination admin (no real Cognito login here;
    # authentication itself is covered by test_auth.py). /health/db stays public.
    app = create_app()
    app.dependency_overrides[require_exam_data_access] = lambda: None
    # psycopg async needs a selector event loop on Windows.
    with TestClient(app, backend_options={"loop_factory": asyncio.SelectorEventLoop}) as client:
        assert client.get("/health/db").json()["status"] == "healthy"
        assert len(client.get("/api/v1/modules").json()) == 42
        assert client.get("/api/v1/students", params={"limit": 1}).json()["total"] == 390
        assert client.get("/api/v1/registrations", params={"registration_type": "repeat"}).json()["total"] == 63
        assert len(client.get("/api/v1/venue-blocks").json()) == 27
