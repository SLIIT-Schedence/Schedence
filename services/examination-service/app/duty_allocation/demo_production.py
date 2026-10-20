"""PRODUCTION READ-ONLY DEMO mode for the SYNTHETIC ``m3_demo`` schema (explicit opt-in only).

The Neon project has no separate development branch: ``m3_demo`` lives in the same database
as the real Schedence schemas. This mode exists so Member 3 can read ONLY ``m3_demo`` there.
It is a different entry point from ``demo_database.read_only_m3_demo`` (which still accepts
only ``member3-development``); nothing in that module was loosened.

Nothing is read or connected unless the caller supplies ALL of:

* an absolute path to an environment file and the NAME of the variable holding the URL;
  the file is parsed for that one variable only (no ``os.environ``, no automatic ``.env``);
* the exact Neon endpoint host and database, confirmed in Neon Console, plus the branch
  name ``production`` and ``confirmed_in_neon_console=True``;
* ``credential_rotated=True`` (the previously exposed password was replaced);
* the name of a DEDICATED read-only role, which must be the user in the URL;
* the exact acknowledgement text ``PRODUCTION_READ_ONLY_ACKNOWLEDGEMENT``.

After connecting, and before any ``m3_demo`` row is read, the role is audited inside the
same read-only REPEATABLE READ transaction (see ``audit_dedicated_role``): it must be the
expected role, not a superuser, may not create anything anywhere, may not write any table,
and may read only ``m3_demo`` tables. A shared owner role fails that audit, so the data is
never read with it. The roles and grants are NOT created here: ``DEDICATED_ROLE_SETUP_SQL``
is text for the database owner to review and run themselves.

Everything is read-only (SELECT only, always rolled back, no commit, no migrations), no URL,
password or file content is ever logged or placed in an error message, and the synthetic
institution stays separate from the real one (Step 9C ``check_institution`` still applies).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from schedence_db.config import normalize_database_url
from schedence_db.exceptions import redact

from .demo_database import SCHEMA, DemoDatabaseError, DemoTargetRefused, SqlRowSource

PRODUCTION_BRANCH = "production"
PRODUCTION_READ_ONLY_ACKNOWLEDGEMENT = "I APPROVE READ-ONLY ACCESS TO m3_demo ON THE PRODUCTION BRANCH"
OWNER_LIKE_ROLES = frozenset({"neondb_owner", "postgres", "admin", "root", "owner"})

# For the database owner to review and run by hand (or the equivalent in Neon Console).
# Never executed by this code: assert_read_only() refuses all of it.
DEDICATED_ROLE_SETUP_SQL = """\
-- Run as the database owner, after rotating the exposed password. Password set in Neon Console.
CREATE ROLE m3_demo_reader LOGIN PASSWORD '<set-a-new-password-in-neon-console>';
ALTER ROLE m3_demo_reader SET default_transaction_read_only = on;
REVOKE ALL ON SCHEMA public FROM m3_demo_reader;
GRANT USAGE ON SCHEMA m3_demo TO m3_demo_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA m3_demo TO m3_demo_reader;
"""

_WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
_USER_SCHEMAS = "n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'"
_TABLE_KINDS = "c.relkind IN ('r', 'p', 'v', 'm', 'f')"


class DemoRoleRefused(DemoDatabaseError):
    """The connecting role has more access than the dedicated read-only role may have."""
    code = "M3_DEMO_ROLE_REFUSED"


class EnvFileError(DemoTargetRefused):
    code = "M3_DEMO_ENV_FILE_REFUSED"


def read_env_variable(env_file: str | Path, variable: str) -> str:
    """Return one variable from an explicitly named env file. Other lines are never kept,
    and neither the value nor any line appears in an error message."""
    if not isinstance(env_file, (str, Path)) or not str(env_file).strip() or type(variable) is not str \
            or not variable.strip():
        raise EnvFileError("An explicit environment-file path and variable name are required.")
    path = Path(env_file)
    if not path.is_absolute():
        raise EnvFileError("The environment-file path must be absolute; it is never discovered automatically.")
    if not path.is_file():
        raise EnvFileError("The environment file was not found.")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise EnvFileError("The environment file could not be read.") from None
    found = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, sep, value = line.partition("=")
        if sep and name.strip() == variable:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            found.append(value)
    if len(found) != 1 or not found[0].strip():
        raise EnvFileError(f"The environment file must define {variable} exactly once with a non-empty value.")
    return found[0]


@dataclass(frozen=True)
class ProductionDemoTarget:
    """A verified target. ``url`` holds credentials and never appears in repr or messages."""
    url: str = field(repr=False)
    expected_role: str
    description: str = ""


def verify_production_demo_target(env_file: str | Path, *, url_variable: str, expected_host: str, expected_database: str,
                                  expected_branch: str, expected_role: str, confirmed_in_neon_console: bool,
                                  credential_rotated: bool, acknowledgement: str) -> ProductionDemoTarget:
    """Refuse unless every explicit production read-only check passes. Only then is the
    env file read, and only for ``url_variable``."""
    if acknowledgement != PRODUCTION_READ_ONLY_ACKNOWLEDGEMENT:
        raise DemoTargetRefused("Production read-only mode needs the exact acknowledgement text. Not connecting.")
    if confirmed_in_neon_console is not True:
        raise DemoTargetRefused("Confirm the exact endpoint in Neon Console first (confirmed_in_neon_console=True). "
                                "Not connecting.")
    if credential_rotated is not True:
        raise DemoTargetRefused("The previously exposed password must be rotated first (credential_rotated=True). "
                                "Not connecting.")
    if expected_branch != PRODUCTION_BRANCH:
        raise DemoTargetRefused(f"This mode is only for the {PRODUCTION_BRANCH!r} Neon branch. Not connecting.")
    if not all(type(v) is str and v.strip() for v in (expected_host, expected_database, expected_role)):
        raise DemoTargetRefused("A confirmed host name, database name and dedicated role name are required.")
    if expected_role.strip().lower() in OWNER_LIKE_ROLES:
        raise DemoTargetRefused("An owner or administrative role is not a dedicated read-only role. Not connecting.")
    raw = read_env_variable(env_file, url_variable)
    try:
        normalized = normalize_database_url(raw)
    except ValueError as exc:
        raise DemoTargetRefused(f"Database URL {redact(str(exc), raw)}") from None
    parsed = make_url(normalized)
    host = (parsed.host or "").lower()
    if host != expected_host.strip().lower():
        raise DemoTargetRefused("The URL host does not match the confirmed endpoint. Not connecting.")
    if parsed.database != expected_database.strip():
        raise DemoTargetRefused("The URL database does not match the confirmed database name. Not connecting.")
    if parsed.username != expected_role.strip():
        raise DemoTargetRefused("The URL user is not the dedicated read-only role. Not connecting.")
    return ProductionDemoTarget(
        parsed.render_as_string(hide_password=False), expected_role.strip(),
        f"{PRODUCTION_BRANCH} (user-confirmed in Neon Console), READ-ONLY {SCHEMA}: host={host}, "
        f"database={parsed.database}, role={expected_role.strip()}")


class ProductionRowSource(SqlRowSource):
    """SqlRowSource whose snapshot begins with the dedicated-role audit (once, before any read)."""

    def __init__(self, session: AsyncSession, *, expected_role: str, expected_database: str) -> None:
        super().__init__(session)
        self.expected_role, self.expected_database = expected_role, expected_database

    async def begin_read_only(self) -> None:
        await super().begin_read_only()
        await audit_dedicated_role(self, expected_role=self.expected_role, expected_database=self.expected_database)


async def audit_dedicated_role(source: SqlRowSource, *, expected_role: str, expected_database: str) -> None:
    """SELECT-only audit of the connected role; raises DemoRoleRefused before any m3_demo read.
    Messages carry counts and schema names only."""
    who = (await source._execute("SELECT current_user, current_database()")).all()
    if who != [(expected_role, expected_database)]:
        raise DemoRoleRefused("The connection is not the expected dedicated role and database; stopping.")
    flags = (await source._execute("SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls FROM pg_roles "
                                   "WHERE rolname = current_user")).all()
    if flags != [(False, False, False, False)]:
        raise DemoRoleRefused("The role has administrative attributes (superuser, create role/db or bypass RLS).")
    can_create = (await source._execute(
        f"SELECT n.nspname FROM pg_namespace n WHERE {_USER_SCHEMAS} "
        f"AND has_schema_privilege(current_user, n.oid, :privilege) ORDER BY 1", {"privilege": "CREATE"})).all()
    if can_create:
        raise DemoRoleRefused("The role can create objects in: " + ", ".join(r[0] for r in can_create) + ".")
    usable = {r[0] for r in (await source._execute(
        f"SELECT n.nspname FROM pg_namespace n WHERE {_USER_SCHEMAS} "
        f"AND has_schema_privilege(current_user, n.oid, :privilege)", {"privilege": "USAGE"})).all()}
    if SCHEMA not in usable:
        raise DemoRoleRefused(f"The role cannot use schema {SCHEMA}.")
    extra = usable - {SCHEMA, "public"}
    if extra:
        raise DemoRoleRefused("The role can use other schemas: " + ", ".join(sorted(extra)) + ".")
    for privilege in _WRITE_PRIVILEGES:
        writable = (await source._execute(
            f"SELECT n.nspname, count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE {_USER_SCHEMAS} AND {_TABLE_KINDS} AND has_table_privilege(current_user, c.oid, :privilege) "
            f"GROUP BY 1 ORDER BY 1", {"privilege": privilege})).all()
        if writable:
            raise DemoRoleRefused(f"The role holds {privilege} on tables in: " + ", ".join(r[0] for r in writable) + ".")
    readable = (await source._execute(
        f"SELECT n.nspname, count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        f"WHERE {_USER_SCHEMAS} AND n.nspname <> :schema AND {_TABLE_KINDS} "
        f"AND has_table_privilege(current_user, c.oid, :privilege) GROUP BY 1 ORDER BY 1",
        {"schema": SCHEMA, "privilege": "SELECT"})).all()
    if readable:
        raise DemoRoleRefused("The role can read tables outside m3_demo, in: " + ", ".join(r[0] for r in readable) + ".")


@asynccontextmanager
async def production_read_only_m3_demo(env_file: str | Path, *, url_variable: str, expected_host: str,
                                       expected_database: str, expected_branch: str, expected_role: str,
                                       confirmed_in_neon_console: bool, credential_rotated: bool,
                                       acknowledgement: str) -> AsyncIterator[ProductionRowSource]:
    """Live source for m3_demo on the production branch. Use with ``load_m3_demo_records``.
    The role audit runs inside ``begin_read_only`` before any table is read."""
    target = verify_production_demo_target(
        env_file, url_variable=url_variable, expected_host=expected_host, expected_database=expected_database,
        expected_branch=expected_branch, expected_role=expected_role, confirmed_in_neon_console=confirmed_in_neon_console,
        credential_rotated=credential_rotated, acknowledgement=acknowledgement)
    engine = create_async_engine(target.url, poolclass=NullPool, connect_args={"connect_timeout": 10})
    try:
        async with AsyncSession(engine) as session:
            try:
                yield ProductionRowSource(session, expected_role=target.expected_role,
                                          expected_database=expected_database.strip())
            finally:
                await session.rollback()  # never commit
    finally:
        await engine.dispose()


__all__ = ["DEDICATED_ROLE_SETUP_SQL", "DemoRoleRefused", "EnvFileError", "OWNER_LIKE_ROLES", "PRODUCTION_BRANCH",
           "PRODUCTION_READ_ONLY_ACKNOWLEDGEMENT", "ProductionDemoTarget", "ProductionRowSource",
           "audit_dedicated_role", "production_read_only_m3_demo", "read_env_variable",
           "verify_production_demo_target"]
