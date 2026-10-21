"""Seed safety, replay and reference checks. No connections to production or solvers."""
import copy
import importlib.util
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("dev_seed", ROOT / "scripts/dev_seed.py")
seed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed)
TENANT = "84651d6d-0c8a-470a-a798-5220666fd367"
OTHER = "44dc54c1-35df-4e53-8fc0-95a7d911368b"
STAFF_NUMBER = "DEV-0123456789abcdef"


def column(name, *, identity=""):
    return {"name": name, "type": "text", "identity": identity, "generated": ""}


def seal(bundle):
    for entry in bundle["tables"].values():
        entry["sha256"] = seed.digest(entry["rows"])
    bundle.pop("sha256", None)
    bundle["sha256"] = seed.digest(bundle)
    return bundle


@pytest.fixture
def bundle():
    value = {"format": seed.FORMAT, "institution_id": TENANT,
             "sources": {owner: {"database": "neondb", "neon_project_id": "source-project",
                                 "neon_branch_id": "source-branch", "revisions": ["v1"]}
                         for owner in seed.TABLES},
             "tables": {table: {"columns": [column("id")], "rows": []} for table in seed.ALL_TABLES},
             "references": {"published_exams": {"sessions": [], "rooms": []}}}
    value["tables"]["examinations.exam_dataset_owner"] = {
        "columns": [column("singleton"), column("institution_id")],
        "rows": [{"singleton": True, "institution_id": TENANT}]}
    return seal(value)


def add_row(bundle, table, row):
    entry = bundle["tables"][table]
    entry["columns"] = [column(name) for name in row]
    entry["rows"].append(row)
    seal(bundle)


def test_complete_empty_dataset_is_valid_and_hash_stable(bundle):
    seed.validate_bundle(bundle)
    assert seed.digest({"a": 1, "b": 2}) == seed.digest({"b": 2, "a": 1})


def test_tampered_bundle_is_rejected(bundle):
    bundle["institution_id"] = OTHER
    with pytest.raises(seed.SeedError, match="checksum"):
        seed.validate_bundle(bundle)


def test_cross_tenant_row_rejected_even_with_recomputed_hash(bundle):
    add_row(bundle, "academic.staff", {"id": "staff", "institution_id": OTHER})
    with pytest.raises(seed.SeedError, match="Cross-institution"):
        seed.validate_bundle(bundle)


def test_unapproved_table_rejected(bundle):
    bundle["tables"]["identity.users"] = {"columns": [], "rows": []}
    seal(bundle)
    with pytest.raises(seed.SeedError, match="unapproved"):
        seed.validate_bundle(bundle)


def test_missing_parent_department_rejected(bundle):
    add_row(bundle, "academic.staff", {"id": "staff", "staff_number": STAFF_NUMBER,
            "full_name": "Development Lecturer 0123456789abcdef", "institution_id": TENANT})
    add_row(bundle, "academic.staff_teaching_profiles", {
        "id": "profile", "staff_number": STAFF_NUMBER, "department_id": "absent", "institution_id": TENANT})
    with pytest.raises(seed.SeedError, match="department_id"):
        seed.validate_bundle(bundle)


def test_student_groups_may_have_nullable_parent_but_not_missing_parent(bundle):
    add_row(bundle, "academic.cohort_totals", {"id": "cohort", "institution_id": TENANT})
    add_row(bundle, "academic.specializations", {"id": "specialization", "institution_id": TENANT})
    add_row(bundle, "academic.pp1_specialization_allocations", {
        "id": "allocation", "cohort_total_id": "cohort", "specialization_id": "specialization", "institution_id": TENANT})
    add_row(bundle, "academic.pp1_student_groups", {
        "id": "group", "allocation_id": "allocation", "parent_id": None, "institution_id": TENANT})
    seed.validate_bundle(bundle)
    bundle["tables"]["academic.pp1_student_groups"]["rows"][0]["parent_id"] = "absent"
    seal(bundle)
    with pytest.raises(seed.SeedError, match="parent_id"):
        seed.validate_bundle(bundle)


def test_missing_published_exam_session_rejected(bundle):
    bundle["references"]["published_exams"]["rooms"] = [{"timetable_id": 1, "session_id": "missing"}]
    seal(bundle)
    with pytest.raises(seed.SeedError, match="Exam room"):
        seed.validate_bundle(bundle)


def test_exam_owner_must_be_same_single_tenant(bundle):
    bundle["tables"]["examinations.exam_dataset_owner"]["rows"][0]["institution_id"] = OTHER
    seal(bundle)
    with pytest.raises(seed.SeedError, match="Cross-institution"):
        seed.validate_bundle(bundle)


def test_snapshot_cannot_be_written_outside_ignored_directory():
    for path in (ROOT / "seed.json", seed.PRIVATE / "../seed.json", seed.PRIVATE / "target.example.json"):
        with pytest.raises(seed.SeedError):
            seed.private_path(path)


def test_url_requires_explicit_file_and_rejects_overriding_options(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("DIRECT_DATABASE_URL=postgresql://reader:secret@localhost/dev?options=unsafe")
    with pytest.raises(seed.SeedError, match="options"):
        seed.read_url(path, "DIRECT_DATABASE_URL")
    path.write_text("")
    monkeypatch.setenv("DIRECT_DATABASE_URL", "postgresql://owner:secret@production/neondb")
    with pytest.raises(seed.SeedError, match="missing"):
        seed.read_url(path, "DIRECT_DATABASE_URL")


def target(database="schedence_dev", branch="new-dev-branch", project="dev-project"):
    actual = {"database": database, "neon_project_id": project, "neon_branch_id": branch}
    return {"environment": "development", **actual}, actual


@pytest.mark.parametrize("database,branch", [("neondb", "new-dev-branch"), ("schedence_dev", "source-branch"),
                                          ("schedence_dev", "br-dry-darkness-b549ci92")])
def test_existing_neon_database_and_source_or_protected_branches_rejected(bundle, database, branch):
    manifest, actual = target(database, branch)
    with pytest.raises(seed.SeedError, match="protected"):
        seed.guard_target(make_url(f"postgresql://seed:secret@dev.neon.tech/{database}"), manifest, actual, bundle)


def test_target_manifest_cannot_misidentify_database(bundle):
    manifest, actual = target()
    actual["neon_branch_id"] = "unexpected-branch"
    with pytest.raises(seed.SeedError, match="identity"):
        seed.guard_target(make_url("postgresql://seed:secret@dev.neon.tech/schedence_dev"), manifest, actual, bundle)


def test_new_explicit_neon_dev_target_passes(bundle):
    manifest, actual = target()
    seed.guard_target(make_url("postgresql://seed:secret@dev.neon.tech/schedence_dev"), manifest, actual, bundle)


@pytest.mark.parametrize("host,database,accepted", [("127.0.0.1", "schedence_seed_test", True),
    ("localhost", "schedence_seed_test", False), ("127.0.0.1", "schedence_step12b4_test", False),
    ("remote", "schedence_seed_test", False)])
def test_local_target_cannot_touch_existing_isolated_history(bundle, host, database, accepted):
    manifest, actual = target(database, branch=None, project=None)
    url = make_url(f"postgresql://seed:secret@{host}/{database}")
    if accepted:
        seed.guard_target(url, manifest, actual, bundle)
    else:
        with pytest.raises(seed.SeedError, match="Local targets"):
            seed.guard_target(url, manifest, actual, bundle)


class Result:
    def __init__(self, one=None, many=None):
        self.one, self.many = one, many

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class Connection:
    def __init__(self, *, elevated=False, institution=True, owns_tables=False):
        self.statements = []
        self.elevated, self.institution = elevated, institution
        self.owns_tables = owns_tables

    def execute(self, query, params=()):
        query = query.as_string() if hasattr(query, "as_string") else query
        self.statements.append((query, params))
        if "rolsuper" in query:
            return Result(one=(self.elevated,))
        if "c.relowner" in query:
            return Result(one=(self.owns_tables,))
        if "identity.institutions" in query:
            return Result(one=(UUID(TENANT),) if self.institution else None)
        if "version_num" in query:
            return Result(many=[("v1",)])
        return Result()


def setup_plan(monkeypatch, bundle, current):
    monkeypatch.setattr(seed, "columns", lambda conn, table: bundle["tables"][table]["columns"])
    monkeypatch.setattr(seed, "rows", lambda conn, table, tenant, **kw: current.get(table, []))


def test_replay_is_unchanged_and_new_rows_are_insert_only(bundle, monkeypatch):
    current = {table: copy.deepcopy(entry["rows"]) for table, entry in bundle["tables"].items()}
    setup_plan(monkeypatch, bundle, current)
    assert set(seed.plan_tables(Connection(), bundle).values()) == {"unchanged"}
    current.clear()
    plan = seed.plan_tables(Connection(), bundle)
    assert plan["examinations.exam_dataset_owner"] == "insert"


def test_conflicting_existing_data_never_overwritten(bundle, monkeypatch):
    setup_plan(monkeypatch, bundle, {"academic.staff": [{"id": "existing"}]})
    with pytest.raises(seed.SeedError, match="no updates/deletes"):
        seed.plan_tables(Connection(), bundle)


@pytest.mark.parametrize("elevated,institution,message", [(True, True, "restricted"), (False, False, "Identity owner")])
def test_loader_requires_restricted_role_and_canonical_identity(bundle, elevated, institution, message):
    with pytest.raises(seed.SeedError, match=message):
        seed.plan_tables(Connection(elevated=elevated, institution=institution), bundle)


def test_table_owner_cannot_bypass_rls_during_load(bundle):
    with pytest.raises(seed.SeedError, match="ownership"):
        seed.plan_tables(Connection(owns_tables=True), bundle)


def test_load_defaults_to_readonly_and_apply_uses_only_allowlisted_inserts(bundle, monkeypatch):
    setup_plan(monkeypatch, bundle, {})
    manifest, actual = target()
    conn = Connection()
    modes = []

    @contextmanager
    def connect(url, *, readonly=True):
        modes.append(readonly)
        yield conn

    monkeypatch.setattr(seed, "connect", connect)
    monkeypatch.setattr(seed, "database_identity", lambda conn: actual)
    url = make_url("postgresql://seed:secret@dev.neon.tech/schedence_dev")
    seed.load_bundle(url, manifest, bundle)
    assert modes == [True]
    assert not any("INSERT" in query or "LOCK TABLE" in query for query, _ in conn.statements)
    conn.statements.clear()
    seed.load_bundle(url, manifest, bundle, apply=True)
    assert modes == [True, False]
    inserts = [query for query, _ in conn.statements if query.startswith("INSERT")]
    assert len(inserts) == 1
    assert '"examinations"."exam_dataset_owner"' in inserts[0]
    assert not any(word in query for query, _ in conn.statements for word in ("DELETE", "UPDATE", "TRUNCATE", "DROP", "DISABLE"))


def test_identity_sequence_columns_are_not_replayed():
    entry = {"columns": [column("block_id", identity="a"), column("venue_id")]}
    assert seed.normalized_rows(entry, [{"block_id": 99, "venue_id": "V1"}]) == [{"venue_id": "V1"}]


def test_cli_suppresses_connection_secrets(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(seed, "read_url", lambda *args: (_ for _ in ()).throw(ValueError("secret-password")))
    args = ["export", "--institution-id", TENANT, "--output", str(seed.PRIVATE / "test-error.json")]
    for owner in seed.TABLES:
        args += [f"--{owner}-env-file", str(tmp_path / ".env"), f"--{owner}-url-key", "URL"]
    assert seed.main(args) == 1
    assert "secret-password" not in capsys.readouterr().out


def test_sanitization_preserves_staff_references_without_live_identity_or_names():
    staff = {"id": OTHER, "institution_id": TENANT, "staff_number": "REAL-001",
             "full_name": "Private Name", "email": "private@example.com", "identity_user_id": OTHER}
    profile = {"staff_number": "REAL-001", "source_values": {"Name": "Private Name"},
               "source_position": "Private Name", "record_key": "Private Name"}
    safe_staff = seed.sanitize_rows("academic.staff", [staff], TENANT)[0]
    safe_profile = seed.sanitize_rows("academic.staff_teaching_profiles", [profile], TENANT)[0]
    assert safe_staff["staff_number"] == safe_profile["staff_number"]
    assert safe_staff["id"] != OTHER
    assert safe_staff["email"] is None and safe_staff["identity_user_id"] is None
    assert "Private Name" not in seed.canonical([safe_staff, safe_profile])
    assert staff["full_name"] == "Private Name"  # input/source data never mutated


def test_student_registration_keys_are_consistently_pseudonymized_and_short_enough():
    student = seed.sanitize_rows("examinations.seed_students", [{"student_id": "REAL-STUDENT"}], TENANT)[0]
    registration = seed.sanitize_rows("examinations.seed_registrations", [{"student_id": "REAL-STUDENT"}], TENANT)[0]
    assert student == registration
    assert len(student["student_id"]) == 16 and student["student_id"] != "REAL-STUDENT"


def test_preference_responses_never_exported():
    assert seed.sanitize_rows("academic.lecturer_preferences", [{"private": "response"}], TENANT) == []


def test_validly_hashed_bundle_with_live_contact_data_is_rejected(bundle):
    add_row(bundle, "academic.staff", {"id": OTHER, "institution_id": TENANT, "staff_number": STAFF_NUMBER,
            "full_name": "Private Name", "email": "private@example.com", "identity_user_id": OTHER})
    with pytest.raises(seed.SeedError, match="contact/account"):
        seed.validate_bundle(bundle)


def test_metadata_connection_must_match_source_database_before_reading_records(monkeypatch):
    @contextmanager
    def connect(url):
        yield Connection()

    monkeypatch.setattr(seed, "connect", connect)
    monkeypatch.setattr(seed, "database_identity", lambda conn: {"database": "different"})
    with pytest.raises(seed.SeedError, match="Metadata connection"):
        seed.export_owner({}, "academic", None, TENANT, {"identity": {"database": "source"}})


def test_exam_tenant_owner_checked_before_any_dataset_export(monkeypatch):
    conn = Connection()
    execute = conn.execute

    def exam_execute(query, params=()):
        if isinstance(query, str) and "SELECT institution_id" in query:
            return Result(many=[(UUID(OTHER),)])
        return execute(query, params)

    conn.execute = exam_execute

    @contextmanager
    def connect(url):
        yield conn

    monkeypatch.setattr(seed, "connect", connect)
    monkeypatch.setattr(seed, "database_identity", lambda conn: {})
    monkeypatch.setattr(seed, "published_exams", lambda *args: pytest.fail("Must not read another tenant"))
    with pytest.raises(seed.SeedError, match="belongs to another"):
        seed.export_owner({"sources": {}}, "examinations", None, TENANT,
                          {"identity": {}, "revisions": {"examinations": ["v1"]}})


def test_connection_enforces_readonly_and_utc_before_any_caller_queries(monkeypatch):
    conn = Connection()
    settings = []

    @contextmanager
    def pg_connect(dsn, **kwargs):
        settings.append(kwargs)
        yield conn

    monkeypatch.setattr(seed.psycopg, "connect", pg_connect)
    with seed.connect(make_url("postgresql://reader:secret@localhost/dev")):
        pass
    assert "default_transaction_read_only=on" in settings[0]["options"]
    assert "timezone=UTC" in settings[0]["options"]
    assert [q for q, _ in conn.statements] == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ", "SET TRANSACTION READ ONLY"]
