"""The API's sessions are read-only and never commit."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.database import close_database, get_session, open_database


async def test_session_factory_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # Creating the engine opens no connection, so a placeholder URL is enough.
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@db.example.invalid/neondb")
    app = FastAPI()
    await open_database(app)
    try:
        bind = app.state.session_factory.kw["bind"]
        assert bind.get_execution_options()["postgresql_readonly"] is True
        assert "postgresql_readonly" not in app.state.engine.get_execution_options()
        assert bind.pool is app.state.engine.pool  # one shared pool, not a second connection system
    finally:
        await close_database(app)


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def commit(self) -> None:
        self.calls.append("COMMIT")

    async def rollback(self) -> None:
        self.calls.append("ROLLBACK")

    async def close(self) -> None:
        self.calls.append("CLOSE")


def _request(session: FakeSession):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_factory=lambda: session)))


async def test_session_is_closed_and_never_committed() -> None:
    session = FakeSession()
    dependency = get_session(_request(session))  # type: ignore[arg-type]
    assert await anext(dependency) is session
    with pytest.raises(StopAsyncIteration):  # FastAPI finishes the dependency like this
        await anext(dependency)
    assert session.calls == ["CLOSE"]


async def test_session_rolls_back_and_closes_on_error() -> None:
    session = FakeSession()
    dependency = get_session(_request(session))  # type: ignore[arg-type]
    await anext(dependency)
    with pytest.raises(RuntimeError):
        await dependency.athrow(RuntimeError("boom"))
    assert session.calls == ["ROLLBACK", "CLOSE"]
