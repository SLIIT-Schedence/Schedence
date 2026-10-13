"""Shared plumbing for operator commands (run from services/identity-service)."""

from __future__ import annotations

import getpass
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.config import SERVICE_NAME
from app.repositories.sql import SqlIdentityUnitOfWork
from schedence_db import create_db_engine, create_session_factory, describe_db_error, dispose_engine, load_settings, session_scope

EXIT_OK, EXIT_REFUSED, EXIT_CONFIG = 0, 1, 2

#: Operator commands are interactive and rare; give a suspended Neon compute time to wake up.
OPERATOR_CONNECT_TIMEOUT_SECONDS = 30


@asynccontextmanager
async def operator_unit_of_work() -> AsyncIterator[SqlIdentityUnitOfWork]:
    """One transaction on the *direct* Neon endpoint (falls back to DATABASE_URL)."""
    settings = load_settings()
    if settings.db_application_name is None:
        settings = settings.model_copy(update={"db_application_name": f"{SERVICE_NAME}-operator"})
    timeout = max(settings.db_connect_timeout, OPERATOR_CONNECT_TIMEOUT_SECONDS)
    engine = create_db_engine(settings, direct=True, connect_args={"connect_timeout": timeout})
    try:
        async with session_scope(create_session_factory(engine), commit=False) as session:
            yield SqlIdentityUnitOfWork(session)
    finally:
        await dispose_engine(engine)


def operator_label() -> str:
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"[:120]
    except Exception:
        return "unknown-operator"


def fail(message: str, code: int = EXIT_REFUSED) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return code


def database_failure(exc: BaseException) -> int:
    """Report a driver/network error without its raw text (which can name hosts and users)."""
    return fail(f"Database error: {describe_db_error(exc)}. Nothing was committed. "
                "If the Neon compute was idle, wait a few seconds and run the command again.")


def unexpected_failure(exc: BaseException) -> int:
    return fail(f"Unexpected {type(exc).__name__}; nothing was committed. Re-run with --debug to see the traceback.")
