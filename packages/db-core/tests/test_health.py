from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from support import FAKE_PASSWORD, FAKE_USER, assert_no_secrets, local_url

from schedence_db.config import load_settings
from schedence_db.connection import create_db_engine
from schedence_db.exceptions import describe_db_error
from schedence_db.health import DatabaseHealth, check_database


def result_with(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


def session_returning(value: Any = 1, *, error: BaseException | None = None) -> MagicMock:
    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=result_with(value), side_effect=error)
    return session


def driver_error(message: str) -> OperationalError:
    """An OperationalError shaped like SQLAlchemy's wrapper around a psycopg error."""
    return OperationalError("SELECT 1", {}, Exception(message))


async def test_healthy_session() -> None:
    session = session_returning(1)

    health = await check_database(session)

    assert health.healthy is True
    assert health.error is None
    assert health.latency_ms is not None and health.latency_ms >= 0
    executed = session.execute.await_args.args[0]
    assert str(executed) == "SELECT 1"


async def test_healthy_engine_checks_out_and_returns_a_connection() -> None:
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=result_with(1))
    returned = []

    @asynccontextmanager
    async def connect():  # type: ignore[no-untyped-def]
        yield connection
        returned.append(True)

    engine = MagicMock(spec=AsyncEngine)
    engine.connect = connect

    health = await check_database(engine)

    assert health.healthy is True
    assert returned == [True]


async def test_authentication_failure_is_reported_without_credentials(caplog: pytest.LogCaptureFixture) -> None:
    error = driver_error(
        f'connection to server at "ep-example-pooler.neon.tech" failed: FATAL: password authentication '
        f'failed for user "{FAKE_USER}" (password {FAKE_PASSWORD})'
    )

    health = await check_database(session_returning(error=error))

    assert health.healthy is False
    assert health.latency_ms is None
    assert health.error is not None and "authentication failed" in health.error
    for text in (health.error, str(health.to_dict()), caplog.text):
        assert_no_secrets(text)
        assert FAKE_USER not in text
        assert "neon.tech" not in text


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ('could not translate host name "ep-x.neon.tech" to address', "could not be resolved"),
        ("connection timeout expired", "timed out"),
        ("connection refused", "refused"),
        ("SSL SYSCALL error: EOF detected", "SSL/TLS"),
        ("ERROR: Endpoint ID is not specified", "Neon endpoint"),
        ("FATAL: sorry, too many connections", "too many connections"),
        ("FATAL: remaining connection slots are reserved", "too many connections"),
        ('FATAL: database "schedence" does not exist', "does not exist"),
    ],
)
async def test_connection_failures_are_classified(message: str, expected: str) -> None:
    health = await check_database(session_returning(error=driver_error(message)))

    assert health.healthy is False
    assert health.error is not None and expected in health.error


async def test_timeout_is_reported_as_unhealthy() -> None:
    session = MagicMock(spec=AsyncSession)

    async def slow_execute(*_: Any, **__: Any) -> None:
        await asyncio.sleep(5)

    session.execute = slow_execute

    health = await check_database(session, timeout=0.05)

    assert health.healthy is False
    assert health.error is not None and "timed out" in health.error


async def test_unexpected_result_is_unhealthy() -> None:
    health = await check_database(session_returning(42))
    assert health.healthy is False
    assert health.error == "unexpected result from SELECT 1"


async def test_programming_errors_are_not_swallowed() -> None:
    with pytest.raises(TypeError):
        await check_database(session_returning(error=TypeError("bug in caller")))


async def test_unreachable_local_server_is_unhealthy(closed_port: int) -> None:
    # Real psycopg connection attempt against a local port with no server.
    engine = create_db_engine(
        load_settings(env_file=False, database_url=local_url(closed_port), db_connect_timeout=3)
    )
    try:
        health = await check_database(engine, timeout=10)
    finally:
        await engine.dispose()

    assert health.healthy is False
    assert health.error is not None
    assert "refused" in health.error or "timed out" in health.error
    assert_no_secrets(health.error)


def test_health_result_as_dict() -> None:
    assert DatabaseHealth(healthy=True, latency_ms=12.3).to_dict() == {
        "status": "healthy",
        "latency_ms": 12.3,
        "error": None,
    }
    assert DatabaseHealth(healthy=False, error="x").status == "unhealthy"


def test_windows_proactor_loop_error_is_explained() -> None:
    error = InterfaceError("connect", {}, Exception("Psycopg cannot use the 'ProactorEventLoop' to run in async mode."))
    assert "selector event loop" in describe_db_error(error)


def test_unknown_errors_fall_back_to_the_exception_type() -> None:
    class WeirdDriverError(Exception):
        pass

    error = OperationalError("SELECT 1", {}, WeirdDriverError(f"weird {FAKE_PASSWORD}"))
    description = describe_db_error(error)
    assert description == "database error (WeirdDriverError)"
    assert_no_secrets(description)
