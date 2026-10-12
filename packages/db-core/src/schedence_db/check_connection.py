"""Command-line connectivity check.

Usage (PowerShell)::

    python -m schedence_db.check_connection [--env-file PATH] [--direct] [--timeout SECONDS]

Exit codes: 0 healthy, 1 unhealthy, 2 configuration error.
Prints no user names, passwords, host names or connection strings.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.pool import NullPool

from schedence_db.config import DatabaseSettings, load_settings
from schedence_db.connection import create_db_engine, dispose_engine
from schedence_db.exceptions import DatabaseConfigurationError
from schedence_db.health import HEALTH_QUERY, DatabaseHealth, check_database
from schedence_db.runtime import run

EXIT_HEALTHY, EXIT_UNHEALTHY, EXIT_CONFIG_ERROR = 0, 1, 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    print("Schedence Database Connection Test\n")

    try:
        settings = load_settings(args.env_file)
    except DatabaseConfigurationError as exc:
        print("Configuration: Failed")
        print(exc)
        return EXIT_CONFIG_ERROR
    if args.direct and not settings.has_direct_url:
        print("Configuration: Failed")
        print("--direct was requested but DIRECT_DATABASE_URL is not set.")
        return EXIT_CONFIG_ERROR

    print("Configuration: Loaded")
    print(f"Endpoint: {'direct (DIRECT_DATABASE_URL)' if args.direct else 'pooled (DATABASE_URL)'}")

    health, server_version = run(_probe(settings, direct=args.direct, timeout=args.timeout))

    print(f"Database: {_describe_server(server_version)}")
    print(f"Connection: {'Successful' if health.healthy else 'Failed'}")
    print(f"Query: {HEALTH_QUERY}")
    if health.healthy:
        print(f"Latency: {health.latency_ms} ms")
    else:
        print(f"Reason: {health.error}")
    print(f"Status: {health.status.capitalize()}")
    return EXIT_HEALTHY if health.healthy else EXIT_UNHEALTHY


async def _probe(
    settings: DatabaseSettings, *, direct: bool, timeout: float
) -> tuple[DatabaseHealth, tuple[int, ...] | None]:
    # NullPool: the single connection is closed as soon as the check finishes.
    engine = create_db_engine(settings, direct=direct, poolclass=NullPool)
    try:
        health = await check_database(engine, timeout=timeout)
        version = engine.dialect.server_version_info if health.healthy else None
        return health, version
    finally:
        await dispose_engine(engine)


def _describe_server(version: tuple[int, ...] | None) -> str:
    if not version:
        return "PostgreSQL"
    return "PostgreSQL " + ".".join(str(part) for part in version)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m schedence_db.check_connection",
        description="Check connectivity to the configured Schedence database with SELECT 1.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to a .env file (default: $SCHEDENCE_ENV_FILE, else the nearest .env up to the repository root).",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Check DIRECT_DATABASE_URL instead of the pooled DATABASE_URL.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Seconds to wait (default: 20, enough for a suspended Neon compute to wake).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
