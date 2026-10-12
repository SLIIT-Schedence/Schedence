from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool
from support import DIRECT_URL, FAKE_PASSWORD, REMOTE_URL, assert_no_secrets

from schedence_db.config import DatabaseSettings, load_settings
from schedence_db.connection import create_db_engine, dispose_engine
from schedence_db.exceptions import DatabaseConfigurationError


def make_settings(**overrides: object) -> DatabaseSettings:
    return load_settings(env_file=False, database_url=REMOTE_URL, **overrides)


def driver_connect_kwargs(engine: AsyncEngine) -> dict[str, object]:
    # The keyword arguments the dialect derives from the URL for psycopg's connect().
    _, kwargs = engine.dialect.create_connect_args(engine.url)
    return kwargs


async def test_creates_async_psycopg_engine_with_bounded_pool() -> None:
    engine = create_db_engine(make_settings())
    try:
        assert isinstance(engine, AsyncEngine)
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
        assert engine.dialect.is_async
        pool = engine.pool
        assert isinstance(pool, AsyncAdaptedQueuePool)
        assert pool.size() == 5
        assert pool._max_overflow == 5
        assert pool._timeout == 30
        assert pool._recycle == 1800
        assert pool._pre_ping is True
    finally:
        await engine.dispose()


async def test_pool_settings_come_from_configuration() -> None:
    settings = make_settings(db_pool_size=2, db_max_overflow=1, db_pool_timeout=7, db_pool_recycle=300)
    engine = create_db_engine(settings)
    try:
        assert (engine.pool.size(), engine.pool._max_overflow) == (2, 1)
        assert (engine.pool._timeout, engine.pool._recycle) == (7, 300)
    finally:
        await engine.dispose()


async def test_tls_parameters_from_the_url_reach_the_driver() -> None:
    engine = create_db_engine(make_settings())
    try:
        kwargs = driver_connect_kwargs(engine)
        assert kwargs["sslmode"] == "require"
        assert kwargs["channel_binding"] == "require"
    finally:
        await engine.dispose()


@pytest.fixture
def captured_engine_options(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Replace create_async_engine and record the options it receives."""
    captured: dict[str, object] = {}

    def fake_create_async_engine(url: object, **options: object) -> MagicMock:
        captured.update(options)
        return MagicMock(spec=AsyncEngine)

    monkeypatch.setattr("schedence_db.connection.create_async_engine", fake_create_async_engine)
    return captured


def test_connect_args_come_from_settings_and_are_merged(captured_engine_options: dict[str, object]) -> None:
    settings = make_settings(db_connect_timeout=15, db_application_name="identity-service")

    create_db_engine(settings, connect_args={"options": "-c statement_timeout=5000"})

    assert captured_engine_options["connect_args"] == {
        "connect_timeout": 15,
        "application_name": "identity-service",
        "options": "-c statement_timeout=5000",
    }


def test_prepared_statements_can_be_disabled_for_poolers(captured_engine_options: dict[str, object]) -> None:
    create_db_engine(make_settings(db_disable_prepared_statements=True))

    assert captured_engine_options["connect_args"] == {"connect_timeout": 10, "prepare_threshold": None}


async def test_testing_environment_uses_null_pool() -> None:
    engine = create_db_engine(make_settings(app_env="testing"))
    try:
        assert isinstance(engine.pool, NullPool)
    finally:
        await engine.dispose()


async def test_direct_engine_uses_direct_url_and_null_pool() -> None:
    engine = create_db_engine(make_settings(direct_database_url=DIRECT_URL), direct=True)
    try:
        assert engine.url.host == "ep-example-123456.eu-central-1.aws.neon.tech"
        assert isinstance(engine.pool, NullPool)
    finally:
        await engine.dispose()


async def test_caller_options_override_settings() -> None:
    engine = create_db_engine(make_settings(), poolclass=NullPool, echo=True)
    try:
        assert isinstance(engine.pool, NullPool)
        assert engine.echo is True
    finally:
        await engine.dispose()


async def test_each_call_creates_an_independent_engine() -> None:
    settings = make_settings()
    first, second = create_db_engine(settings), create_db_engine(settings)
    try:
        assert first is not second
        assert first.pool is not second.pool
    finally:
        await first.dispose()
        await second.dispose()


async def test_settings_are_loaded_when_not_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", REMOTE_URL)
    engine = create_db_engine()
    try:
        assert engine.url.database == "neondb"
    finally:
        await engine.dispose()


async def test_engine_and_url_representations_hide_the_password() -> None:
    engine = create_db_engine(make_settings())
    try:
        assert engine.url.password == FAKE_PASSWORD
        assert_no_secrets(repr(engine))
        assert_no_secrets(str(engine.url))
        assert_no_secrets(repr(engine.url))
    finally:
        await engine.dispose()


def test_invalid_engine_options_raise_configuration_error_without_secrets() -> None:
    with pytest.raises(DatabaseConfigurationError, match="Could not create the database engine") as exc_info:
        create_db_engine(make_settings(), not_a_real_option=True)
    assert_no_secrets(str(exc_info.value))


async def test_dispose_engine_closes_the_pool() -> None:
    engine = MagicMock(spec=AsyncEngine)
    engine.dispose = AsyncMock()

    await dispose_engine(engine)

    engine.dispose.assert_awaited_once()


async def test_dispose_engine_accepts_none() -> None:
    await dispose_engine(None)


async def test_dispose_engine_logs_instead_of_raising(caplog: pytest.LogCaptureFixture) -> None:
    engine = MagicMock(spec=AsyncEngine)
    engine.dispose = AsyncMock(side_effect=OSError(f"boom {FAKE_PASSWORD}"))

    await dispose_engine(engine)

    assert "Error while disposing" in caplog.text
    assert_no_secrets(caplog.text)
