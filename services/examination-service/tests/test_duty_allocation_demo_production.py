"""PRODUCTION READ-ONLY DEMO mode: opt-in, refusals and the dedicated-role audit.

Everything is mocked (fake host, fake password, temporary env files, scripted sessions).
Nothing connects to a database.
"""

from __future__ import annotations

import uuid

import pytest

from app.duty_allocation import demo_production
from app.duty_allocation.demo_database import (
    BEGIN_SNAPSHOT, TABLE_COLUMNS, DemoTargetRefused, ReadOnlyViolation, assert_read_only, load_m3_demo_records,
    verify_demo_target,
)
from app.duty_allocation.demo_mapper import DemoDataError, check_institution
from app.duty_allocation.demo_production import (
    DEDICATED_ROLE_SETUP_SQL, PRODUCTION_READ_ONLY_ACKNOWLEDGEMENT, DemoRoleRefused, EnvFileError, ProductionRowSource,
    audit_dedicated_role, production_read_only_m3_demo, read_env_variable, verify_production_demo_target,
)
from duty_allocation_demo_support import INST, compact_source

HOST, DB, ROLE, PASSWORD = "ep-fake-123.example.neon.tech", "neondb", "m3_demo_reader", "Fake-Pw-9z"
URL = f"postgresql://{ROLE}:{PASSWORD}@{HOST}/{DB}?sslmode=require"


def good(**change):
    args = dict(url_variable="M3_DEMO_DATABASE_URL", expected_host=HOST, expected_database=DB, expected_branch="production",
                expected_role=ROLE, confirmed_in_neon_console=True, credential_rotated=True,
                acknowledgement=PRODUCTION_READ_ONLY_ACKNOWLEDGEMENT)
    args.update(change)
    return args


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / "m3.env"
    path.write_text(f"# comment\nOTHER_SECRET=do-not-read-me\nM3_DEMO_DATABASE_URL={URL}\n", encoding="utf-8")
    return path


# --- Environment file ---------------------------------------------------------------------------------

def test_only_the_named_variable_is_read(env_file):
    assert read_env_variable(env_file, "M3_DEMO_DATABASE_URL") == URL


@pytest.mark.parametrize("line", [f'M3_DEMO_DATABASE_URL="{URL}"', f"M3_DEMO_DATABASE_URL='{URL}'",
                                  f"export M3_DEMO_DATABASE_URL={URL}"])
def test_quotes_and_export_are_understood(tmp_path, line):
    path = tmp_path / "e.env"
    path.write_text(line + "\n", encoding="utf-8")
    assert read_env_variable(path, "M3_DEMO_DATABASE_URL") == URL


def test_environment_file_problems_are_refused_without_leaking(tmp_path, env_file):
    with pytest.raises(EnvFileError, match="absolute"):
        read_env_variable("relative.env", "M3_DEMO_DATABASE_URL")
    with pytest.raises(EnvFileError, match="not found"):
        read_env_variable(tmp_path / "missing.env", "M3_DEMO_DATABASE_URL")
    with pytest.raises(EnvFileError, match="exactly once") as missing:
        read_env_variable(env_file, "ABSENT_VARIABLE")
    assert "do-not-read-me" not in str(missing.value) and PASSWORD not in str(missing.value)
    twice = tmp_path / "twice.env"
    twice.write_text("M3_DEMO_DATABASE_URL=a\nM3_DEMO_DATABASE_URL=b\n", encoding="utf-8")
    with pytest.raises(EnvFileError, match="exactly once"):
        read_env_variable(twice, "M3_DEMO_DATABASE_URL")
    with pytest.raises(EnvFileError):
        read_env_variable("", "M3_DEMO_DATABASE_URL")
    with pytest.raises(EnvFileError):
        read_env_variable(env_file, "")


def test_the_process_environment_is_never_used(monkeypatch, tmp_path):
    monkeypatch.setenv("M3_DEMO_DATABASE_URL", URL)
    monkeypatch.setenv("DATABASE_URL", URL)
    empty = tmp_path / "empty.env"
    empty.write_text("NOTHING=here\n", encoding="utf-8")
    with pytest.raises(EnvFileError):
        verify_production_demo_target(empty, **good())


# --- Opt-in and refusal ---------------------------------------------------------------------------------

def test_a_fully_confirmed_target_is_accepted_without_exposing_credentials(env_file):
    target = verify_production_demo_target(env_file, **good())
    assert target.url.startswith(f"postgresql+psycopg://{ROLE}:{PASSWORD}@") and target.expected_role == ROLE
    for shown in (repr(target), target.description, str(target)):
        assert PASSWORD not in shown and "do-not-read-me" not in shown and "postgresql" not in shown
    assert "production" in target.description and "m3_demo" in target.description


@pytest.mark.parametrize("change,match", [
    ({"acknowledgement": ""}, "acknowledgement"),
    ({"acknowledgement": PRODUCTION_READ_ONLY_ACKNOWLEDGEMENT.lower()}, "acknowledgement"),
    ({"confirmed_in_neon_console": False}, "Neon Console"),
    ({"confirmed_in_neon_console": "yes"}, "Neon Console"),
    ({"credential_rotated": False}, "rotated"),
    ({"credential_rotated": 1}, "rotated"),
    ({"expected_branch": "member3-development"}, "production"),
    ({"expected_branch": "main"}, "production"),
    ({"expected_host": ""}, "required"),
    ({"expected_database": "  "}, "required"),
    ({"expected_role": ""}, "required"),
    ({"expected_role": "neondb_owner"}, "not a dedicated"),
    ({"expected_role": "POSTGRES"}, "not a dedicated"),
    ({"expected_host": "ep-other.example.neon.tech"}, "host does not match"),
    ({"expected_database": "other"}, "database does not match"),
    ({"expected_role": "someone_else"}, "not the dedicated"),
])
def test_missing_or_wrong_confirmation_is_refused(env_file, change, match):
    with pytest.raises(DemoTargetRefused, match=match) as refused:
        verify_production_demo_target(env_file, **good(**change))
    assert PASSWORD not in str(refused.value) and "postgresql" not in str(refused.value)


def test_the_env_file_is_not_opened_until_every_confirmation_passes(monkeypatch, env_file):
    def opened(*_):
        raise AssertionError("the env file must not be read")
    monkeypatch.setattr(demo_production, "read_env_variable", opened)
    for change in ({"acknowledgement": "no"}, {"confirmed_in_neon_console": False}, {"credential_rotated": False},
                   {"expected_branch": "dev"}, {"expected_role": "neondb_owner"}, {"expected_host": ""}):
        with pytest.raises(DemoTargetRefused):
            verify_production_demo_target(env_file, **good(**change))


async def test_nothing_connects_when_the_opt_in_is_incomplete(monkeypatch, env_file):
    def engine(*_a, **_k):
        raise AssertionError("no engine may be created")
    monkeypatch.setattr(demo_production, "create_async_engine", engine)
    with pytest.raises(DemoTargetRefused):
        async with production_read_only_m3_demo(env_file, **good(credential_rotated=False)):
            pass
    with pytest.raises(EnvFileError):
        async with production_read_only_m3_demo("relative.env", **good()):
            pass


def test_the_development_adapter_still_refuses_production(env_file):
    with pytest.raises(DemoTargetRefused, match="member3-development"):
        verify_demo_target(URL, expected_host=HOST, expected_database=DB, expected_branch="production",
                           confirmed_in_neon_console=True)


def test_the_setup_script_is_text_only_and_never_executable_here():
    assert "m3_demo_reader" in DEDICATED_ROLE_SETUP_SQL and "GRANT SELECT" in DEDICATED_ROLE_SETUP_SQL
    assert "academic" not in DEDICATED_ROLE_SETUP_SQL and "examinations" not in DEDICATED_ROLE_SETUP_SQL
    for statement in (s for s in DEDICATED_ROLE_SETUP_SQL.replace("\n", " ").split(";") if s.strip()):
        with pytest.raises(ReadOnlyViolation):
            assert_read_only(statement.strip())


# --- Dedicated-role audit (scripted session) -----------------------------------------------------------

class AuditSession:
    """Scripted AsyncSession: answers the audit queries; overrides simulate a too-powerful role."""

    def __init__(self, *, who=(ROLE, DB), flags=(False, False, False, False), can_create=(), usage=("m3_demo", "public"),
                 writable=None, readable=()):
        self.who, self.flags, self.can_create, self.usage = who, flags, can_create, usage
        self.writable, self.readable = writable or {}, readable
        self.statements = []

    async def execute(self, clause, params=None):
        sql, params = str(clause), params or {}
        self.statements.append(sql)
        if "current_setting" in sql:
            rows = [("on", "repeatable read")]
        elif "current_user, current_database()" in sql:
            rows = [tuple(self.who)]
        elif "FROM pg_roles" in sql:
            rows = [tuple(self.flags)]
        elif "has_schema_privilege" in sql:
            rows = [(s,) for s in (self.can_create if params["privilege"] == "CREATE" else self.usage)]
        elif "has_table_privilege" in sql:
            rows = list(self.readable if "schema" in params else self.writable.get(params["privilege"], ()))
        elif "information_schema" in sql:
            rows = [(t, c) for t, cols in TABLE_COLUMNS.items() for c in cols]
        else:
            rows = []
        return type("Result", (), {"all": lambda self_: rows})()

    async def commit(self):
        raise AssertionError("the adapter must never commit")


def read_tables(session):
    return [s for s in session.statements if 'FROM "m3_demo".' in s]


def source(session):
    return ProductionRowSource(session, expected_role=ROLE, expected_database=DB)


async def test_the_dedicated_role_passes_and_only_selects_run():
    session = AuditSession()
    snapshot = await load_m3_demo_records(source(session))
    assert session.statements[0] == BEGIN_SNAPSHOT and all(t in snapshot.tables for t in TABLE_COLUMNS)
    assert all(s.startswith("SELECT") for s in session.statements[1:])
    audit_end = max(i for i, s in enumerate(session.statements) if "has_table_privilege" in s)
    first_read = min(i for i, s in enumerate(session.statements) if 'FROM "m3_demo".' in s)
    assert audit_end < first_read  # the role is audited before any m3_demo row is read
    assert sum("current_setting" in s for s in session.statements) == 1


@pytest.mark.parametrize("override,match", [
    ({"who": ("neondb_owner", DB)}, "expected dedicated role"),
    ({"who": (ROLE, "otherdb")}, "expected dedicated role"),
    ({"flags": (True, False, False, False)}, "administrative"),
    ({"flags": (False, True, False, False)}, "administrative"),
    ({"flags": (False, False, True, False)}, "administrative"),
    ({"flags": (False, False, False, True)}, "administrative"),
    ({"can_create": ("public",)}, "create objects in: public"),
    ({"can_create": ("m3_demo",)}, "create objects in: m3_demo"),
    ({"usage": ("public",)}, "cannot use schema m3_demo"),
    ({"usage": ("m3_demo", "academic")}, "other schemas: academic"),
    ({"usage": ("m3_demo", "examinations", "public")}, "other schemas: examinations"),
    ({"writable": {"INSERT": [("m3_demo", 40)]}}, "INSERT on tables in: m3_demo"),
    ({"writable": {"UPDATE": [("examinations", 3)]}}, "UPDATE on tables in: examinations"),
    ({"writable": {"DELETE": [("academic", 1)]}}, "DELETE on tables in: academic"),
    ({"writable": {"TRUNCATE": [("public", 1)]}}, "TRUNCATE"),
    ({"readable": [("academic", 12)]}, "read tables outside m3_demo, in: academic"),
])
async def test_an_over_privileged_role_is_refused_before_any_read(override, match):
    session = AuditSession(**override)
    with pytest.raises(DemoRoleRefused, match=match):
        await load_m3_demo_records(source(session))
    assert read_tables(session) == [] and not any("information_schema.columns" in s for s in session.statements)


async def test_audit_messages_hold_names_and_counts_only():
    session = AuditSession(writable={"INSERT": [("examinations", 3)]})
    with pytest.raises(DemoRoleRefused) as refused:
        await audit_dedicated_role(source(session), expected_role=ROLE, expected_database=DB)
    assert "postgresql" not in str(refused.value) and PASSWORD not in str(refused.value) and HOST not in str(refused.value)


# --- Synthetic institution stays separate --------------------------------------------------------------

async def test_the_synthetic_institution_is_never_merged_with_a_real_one():
    records = await load_m3_demo_records(compact_source())
    check_institution(records, uuid.UUID(INST))
    with pytest.raises(DemoDataError, match="no single row"):
        check_institution(records, uuid.uuid4())  # e.g. the application's real institution id
