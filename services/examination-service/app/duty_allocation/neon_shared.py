"""Use the existing Neon database: read-only inputs, component-owned saved drafts.

No branch, local database, credential copying or changes to teammates' tables.
Run `check` or `sql` to inspect; only `migrate` creates the component's tables.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from schedence_db import load_settings
from schedence_db.config import DatabaseSettings
from schedence_db.exceptions import DatabaseConfigurationError, describe_db_error
from app.duty_allocation.demo_database import TABLE_COLUMNS, DemoTargetRefused, SqlRowSource
from app.duty_allocation.demo_settings import (
    DemoSource, Member3DemoSettings, PersistenceMode, PERSISTENCE_DATABASE_ACKNOWLEDGEMENT,
)
SERVICE_DIR = Path(__file__).resolve().parents[2]
DUTY_TABLES = ("duty_rosters", "duty_roster_assignments", "duty_roster_events",
               "duty_change_requests", "seating_drafts", "seating_draft_seats")
VERSION_TABLE = "duty_allocation_alembic_version"
OWN_TABLES = {"examinations." + name for name in (*DUTY_TABLES, VERSION_TABLE)}


@dataclass(frozen=True, repr=False)
class NeonConfig:
    demo: Member3DemoSettings
    database: DatabaseSettings


def endpoint(host: str | None) -> str:
    """Treat pooled and direct addresses as the same Neon endpoint."""
    return (host or "").lower().replace("-pooler.", ".")


def verify_shared_database(demo: Member3DemoSettings, database: DatabaseSettings) -> None:
    if demo.source is not DemoSource.NEON_SHARED:
        return
    if demo.missing_setup():
        raise DemoTargetRefused("Missing shared Neon settings: " + ", ".join(demo.missing_setup()))
    if not demo.neon_host.lower().endswith(".neon.tech"):
        raise DemoTargetRefused("NEON_SHARED requires a Neon hostname; local databases are refused.")
    for direct in (False, True):
        target = database.get_url(direct=direct)
        if endpoint(target.host) != endpoint(demo.neon_host) or target.database != demo.neon_database:
            raise DemoTargetRefused("The input, runtime and migration URLs must use the same existing Neon database.")


def load_shared_config(env_file: str | Path | None = None) -> NeonConfig:
    database = load_settings(env_file=env_file)
    demo = Member3DemoSettings(
        source=DemoSource.NEON_SHARED, neon_host=database.get_url().host,
        neon_database=database.get_url().database, persistence=PersistenceMode.DATABASE,
        persistence_acknowledgement=PERSISTENCE_DATABASE_ACKNOWLEDGEMENT,
    )
    verify_shared_database(demo, database)
    return NeonConfig(demo, database)


def inspect_database(config: NeonConfig) -> dict:
    """Read metadata only; do not commit or print credentials or staff/student records."""
    engine = create_engine(config.database.get_url(direct=True), poolclass=NullPool,
                           connect_args={"connect_timeout": 10})
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            tables = set(connection.execute(text(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema IN ('m3_demo', 'examinations')")).all())
            revision = (connection.execute(text("SELECT version_num FROM examinations.alembic_version")).scalars().all()
                        if ("examinations", "alembic_version") in tables else [])
            duty_revision = (connection.execute(text(
                f"SELECT version_num FROM examinations.{VERSION_TABLE}")).scalars().all()
                if ("examinations", VERSION_TABLE) in tables else [])
            connection.rollback()
        return {"revision": revision, "duty_revision": duty_revision,
                "missing_input_tables": sorted(name for name in TABLE_COLUMNS if ("m3_demo", name) not in tables),
                "missing_duty_tables": sorted(name for name in DUTY_TABLES if ("examinations", name) not in tables)}
    finally:
        engine.dispose()


def child_environment(config: NeonConfig) -> dict[str, str]:
    """Apply component settings only to its child process; retain the team's auth configuration."""
    environment = dict(os.environ)
    for key in list(environment):
        if key.upper().startswith("M3_DEMO_") or key.upper() == "SCHEDENCE_ENV_FILE":
            del environment[key]
    for name in Member3DemoSettings.model_fields:
        value = getattr(config.demo, name)
        if value is not None:
            environment["M3_DEMO_" + name.upper()] = str(getattr(value, "value", value))
    environment.update(APP_ENV="development", DB_ECHO="false",
                       DATABASE_URL=config.database.get_url().render_as_string(hide_password=False),
                       DIRECT_DATABASE_URL=config.database.get_url(direct=True).render_as_string(hide_password=False))
    return environment


def migration_config(config: NeonConfig, *, output_buffer=None) -> Config:
    """Use component-owned versions, metadata and history; leave the shared chain unchanged."""
    from app.duty_allocation.persistence.models import DutyBase
    alembic = Config(str(SERVICE_DIR / "alembic.ini"), output_buffer=output_buffer)
    alembic.set_main_option("version_locations", str(Path(__file__).parent / "persistence" / "migrations"))
    alembic.attributes["database_url"] = config.database.get_url(direct=True)
    alembic.attributes["version_table"] = VERSION_TABLE
    alembic.attributes["target_metadata"] = DutyBase.metadata
    return alembic


def render_sql(config: NeonConfig) -> str:
    """Render component SQL offline, without a database connection or shared-history update."""
    buffer = io.StringIO()
    command.upgrade(migration_config(config, output_buffer=buffer), "0013", sql=True)
    return buffer.getvalue()


@asynccontextmanager
async def shared_row_source(settings: Member3DemoSettings) -> AsyncIterator[SqlRowSource]:
    database = load_settings()
    verify_shared_database(settings, database)
    engine = create_async_engine(database.get_url(), poolclass=NullPool, connect_args={"connect_timeout": 10})
    try:
        async with AsyncSession(engine) as session:
            try:
                yield SqlRowSource(session)
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


def assert_component_write(statement: str) -> None:
    """Guard every SQL statement emitted by the component migration's connection.

    SELECT is needed for Alembic catalog checks. Writes are restricted to additive
    table/index creation and migration bookkeeping on the seven exact owned tables.
    No arbitrary SQL is accepted by the command-line interface.
    """
    sql = statement.strip()
    if sql.upper().startswith("SELECT "):
        return
    if re.search(r"\b(?:ALTER|DROP|DELETE|TRUNCATE|GRANT|REVOKE|COPY|CALL|DO)\b", sql, re.I):
        raise DemoTargetRefused("The component migration attempted a forbidden database operation; rolled back.")
    patterns = (
        r"CREATE TABLE\s+(examinations\.\w+)",
        r"CREATE (?:UNIQUE )?INDEX\s+\w+\s+ON\s+(examinations\.\w+)",
        r"INSERT INTO\s+(examinations\.\w+)",
        r"UPDATE\s+(examinations\.\w+)",
    )
    match = next((found for pattern in patterns if (found := re.match(pattern, sql, re.I))), None)
    if match is None or match.group(1).lower() not in OWN_TABLES:
        raise DemoTargetRefused("The component migration attempted to touch a table it does not own; rolled back.")
    if sql.upper().startswith(("INSERT", "UPDATE")) and match.group(1).lower() != "examinations." + VERSION_TABLE:
        raise DemoTargetRefused("Schema setup may write rows only to the component's migration marker.")
    references = re.findall(r"REFERENCES\s+([\w.]+)", sql, re.I)
    if any(name.lower() not in OWN_TABLES for name in references):
        raise DemoTargetRefused("The component migration attempted to reference a teammate-owned table.")


def migrate_component(config: NeonConfig) -> dict:
    """One transaction, component lock and SQL guard; never run the shared Alembic chain."""
    verify_shared_database(config.demo, config.database)
    before = inspect_database(config)
    if before["duty_revision"] == ["0013"] and not before["missing_duty_tables"]:
        return before
    if before["missing_input_tables"]:
        raise DemoTargetRefused("Required m3_demo inputs are missing. No synthetic inputs will be inserted automatically.")
    if before["duty_revision"] or len(before["missing_duty_tables"]) != len(DUTY_TABLES):
        raise DemoTargetRefused("Component tables or history already exist in an unexpected state; nothing was changed.")
    # Review the exact generated SQL before creating an engine capable of writing.
    sql = render_sql(config)
    targets = set(re.findall(r"(?:CREATE TABLE|INSERT INTO|UPDATE|REFERENCES|ON) (examinations\.\w+)", sql))
    if targets != OWN_TABLES or "examinations.alembic_version" in sql:
        raise DemoTargetRefused("The rendered migration is outside the component's allowed tables.")
    engine = create_engine(config.database.get_url(direct=True), poolclass=NullPool,
                           connect_args={"connect_timeout": 10})
    @event.listens_for(engine, "before_cursor_execute")
    def guard(connection, cursor, statement, parameters, context, executemany):
        assert_component_write(statement)
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(7343690013)"))
            # Under our component-only lock, re-check for a concurrently completed install.
            existing = connection.execute(text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'examinations' "
                "AND table_name IN (" + ", ".join(":t" + str(i) for i in range(len(OWN_TABLES))) + ")"),
                {"t" + str(i): name.split(".")[1] for i, name in enumerate(sorted(OWN_TABLES))}).scalars().all()
            if existing:
                raise DemoTargetRefused("Component tables appeared during setup; run check again. No tables were changed.")
            alembic = migration_config(config)
            alembic.attributes["connection"] = connection
            command.upgrade(alembic, "0013")
            shared = connection.execute(text("SELECT version_num FROM examinations.alembic_version")).scalars().all()
            if shared != before["revision"]:
                raise DemoTargetRefused("Shared migration history changed concurrently; component setup rolled back.")
    finally:
        engine.dispose()
    return inspect_database(config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "sql", "migrate", "serve"))
    parser.add_argument("--env-file", type=Path, help="Optional explicit database config; default is the existing root .env.")
    args = parser.parse_args(argv)
    try:
        config = load_shared_config(args.env_file)
        if args.action == "sql":
            print(render_sql(config))
            return 0
        print(f"Target: existing Neon database={config.demo.neon_database}, host={config.demo.neon_host}")
        if args.action == "serve":
            process = [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--port", "8003"]
            if sys.platform == "win32":
                process += ["--loop", "asyncio:SelectorEventLoop"]
            return subprocess.call(process, cwd=SERVICE_DIR, env=child_environment(config))
        report = migrate_component(config) if args.action == "migrate" else inspect_database(config)
        print("Shared examination migration (unchanged):", ", ".join(report["revision"]) or "not initialized")
        print("Component migration:", ", ".join(report["duty_revision"]) or "not initialized")
        print("Missing input tables:", ", ".join(report["missing_input_tables"]) or "none")
        print("Missing component tables:", ", ".join(report["missing_duty_tables"]) or "none")
        return 0 if (not report["missing_input_tables"] and not report["missing_duty_tables"]
                     and report["duty_revision"] == ["0013"]) else 1
    except (DemoTargetRefused, DatabaseConfigurationError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        print("Database operation failed: " + describe_db_error(exc), file=sys.stderr)
        return 2
    except ValueError:
        print("Invalid Neon configuration; credential values are omitted.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
