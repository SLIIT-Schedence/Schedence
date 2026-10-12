from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from support import REMOTE_URL, local_url

from schedence_db.config import load_settings
from schedence_db.connection import create_db_engine
from schedence_db.session import create_session_factory, session_dependency, session_scope


class FakeSession:
    """Records lifecycle calls; stands in for AsyncSession in unit tests."""

    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.close = AsyncMock()


@pytest.fixture
def fake_factory() -> MagicMock:
    return MagicMock(side_effect=FakeSession)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_db_engine(load_settings(env_file=False, database_url=REMOTE_URL))
    yield engine
    await engine.dispose()


# --- factory ----------------------------------------------------------------------


async def test_session_factory_is_bound_to_the_engine(engine: AsyncEngine) -> None:
    factory = create_session_factory(engine)

    assert isinstance(factory, async_sessionmaker)
    assert factory.class_ is AsyncSession
    assert factory.kw["bind"] is engine
    assert factory.kw["expire_on_commit"] is False


async def test_session_factory_options_can_be_overridden(engine: AsyncEngine) -> None:
    factory = create_session_factory(engine, expire_on_commit=True, autoflush=False)
    assert factory.kw["expire_on_commit"] is True
    assert factory.kw["autoflush"] is False


async def test_factory_creates_a_new_session_every_time(engine: AsyncEngine) -> None:
    factory = create_session_factory(engine)
    first, second = factory(), factory()
    try:
        assert first is not second
    finally:
        await first.close()
        await second.close()


# --- session_scope ------------------------------------------------------------------


async def test_scope_commits_and_closes_on_success(fake_factory: MagicMock) -> None:
    async with session_scope(fake_factory) as session:
        pass

    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    session.close.assert_awaited_once()


async def test_scope_without_commit_only_closes(fake_factory: MagicMock) -> None:
    async with session_scope(fake_factory, commit=False) as session:
        pass

    session.commit.assert_not_awaited()
    session.close.assert_awaited_once()


async def test_scope_rolls_back_and_reraises_on_error(fake_factory: MagicMock) -> None:
    with pytest.raises(RuntimeError, match="business rule violated"):
        async with session_scope(fake_factory) as session:
            raise RuntimeError("business rule violated")

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()


async def test_scope_rolls_back_when_commit_fails(fake_factory: MagicMock) -> None:
    commit_error = OperationalError("COMMIT", {}, Exception("server closed the connection"))

    with pytest.raises(OperationalError):
        async with session_scope(fake_factory) as session:
            session.commit.side_effect = commit_error

    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()


async def test_scope_rolls_back_on_cancellation(fake_factory: MagicMock) -> None:
    with pytest.raises(asyncio.CancelledError):
        async with session_scope(fake_factory) as session:
            raise asyncio.CancelledError

    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()


async def test_failed_rollback_does_not_hide_the_original_error(
    fake_factory: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(ValueError, match="original"):
        async with session_scope(fake_factory) as session:
            session.rollback.side_effect = OperationalError("ROLLBACK", {}, Exception("connection lost"))
            raise ValueError("original")

    session.close.assert_awaited_once()
    assert "Rollback failed" in caplog.text


async def test_scope_works_with_a_real_async_session(closed_port: int) -> None:
    # No SQL is executed, so no server is needed: this checks the real
    # AsyncSession API (begin/commit/rollback/close) is driven correctly.
    engine = create_db_engine(load_settings(env_file=False, database_url=local_url(closed_port)))
    factory = create_session_factory(engine)
    try:
        async with session_scope(factory) as session:
            assert isinstance(session, AsyncSession)
        with pytest.raises(LookupError):
            async with session_scope(factory) as session:
                raise LookupError
        assert not session.in_transaction()
    finally:
        await engine.dispose()


# --- session_dependency ---------------------------------------------------------------


async def test_dependency_yields_a_session_and_closes_without_committing(fake_factory: MagicMock) -> None:
    get_session = session_dependency(fake_factory)

    generator = get_session()
    session = await anext(generator)
    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.close.assert_awaited_once()


async def test_dependency_rolls_back_when_the_request_fails(fake_factory: MagicMock) -> None:
    generator = session_dependency(fake_factory)()
    session = await anext(generator)

    with pytest.raises(RuntimeError):
        await generator.athrow(RuntimeError("route failed"))

    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()


async def test_dependency_gives_each_request_its_own_session(fake_factory: MagicMock) -> None:
    get_session = session_dependency(fake_factory)

    first_request, second_request = get_session(), get_session()
    first, second = await anext(first_request), await anext(second_request)
    await first_request.aclose()
    await second_request.aclose()

    assert first is not second
    assert fake_factory.call_count == 2
    first.close.assert_awaited_once()
    second.close.assert_awaited_once()
