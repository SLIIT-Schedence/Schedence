"""Explicit, read-only owner connections for detached scheduling inputs.

No environment reads or connections occur on import. Teaching credentials never
gain master-data privileges: each owner supplies its own restricted reader.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Base
from .ports import PersistenceConflict
from .repositories import SqlTeachingRepository
from .snapshots import canonical_json, content_digest

OWNER_TABLES = {
    "academic": ("staff", "source_datasets", "departments", "staff_teaching_profiles", "specializations",
        "modules", "module_specializations", "module_teaching_teams", "lecturer_preferences", "cohort_totals",
        "pp1_specialization_allocations", "pp1_student_groups", "module_classification_reviews"),
    "resource": ("source_datasets", "venues"),
    "teaching": tuple(table.name for table in Base.metadata.tables.values()),
}


@dataclass(frozen=True)
class DatabaseTarget:
    database: str
    project_id: str
    branch_id: str


@dataclass(frozen=True)
class DatabaseEvidence:
    institution_id: UUID
    snapshot_id: UUID
    document: object
    academic: dict[str, tuple[dict, ...]]
    resource: dict[str, tuple[dict, ...]]
    teaching: dict[str, tuple[dict, ...]]
    security: tuple[dict, ...] = ()

    def fingerprint(self):
        return content_digest(dict(institution_id=self.institution_id, snapshot_id=self.snapshot_id,
            document=self.document, academic=self.academic, resource=self.resource, teaching=self.teaching))


def check_read_security(role, tables, schema, *, require_read_only=True):
    if role["transaction_read_only"] != ("on" if require_read_only else "off") or any(role[key] for key in
        ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "rolreplication", "elevated_membership")):
        raise PersistenceConflict("database generation requires a read-only transaction and a restricted owner role")
    expected = set(OWNER_TABLES[schema])
    found = {row["relname"]: row for row in tables if row["relname"] in expected}
    if set(found) != expected:
        raise PersistenceConflict("a required owner table is missing")
    for name, row in found.items():
        if not row["can_select"] or row["owns_table"]:
            raise PersistenceConflict("owner input reader needs SELECT access without table ownership")
        # Legacy Academic staff predates this integration; do not alter its
        # security or claim that its RLS is enabled. Every query is tenant-bound.
        if schema == "academic" and name == "staff":
            continue
        if not row["relrowsecurity"] or not row["relforcerowsecurity"] or row["policy_count"] != 1:
            raise PersistenceConflict("scheduling inputs require forced institution RLS")
        predicate = row["using_expression"] or ""
        normalized = "".join(predicate.split()).replace("(", "").replace(")", "")
        expected_predicate = "institution_id=NULLIFcurrent_setting'schedence.institution_id'::text,true,''::text::uuid"
        if normalized != expected_predicate or row["check_expression"] != predicate:
            raise PersistenceConflict("institution RLS differs from the reviewed USING/WITH CHECK policy")


async def inspect_owner_security(connection, schema):
    """Shared role/policy inspection for explicit read and write transactions."""
    role = dict((await connection.execute(text("""SELECT rolname,rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication,
        current_setting('transaction_read_only') AS transaction_read_only,
        EXISTS(SELECT 1 FROM pg_roles r WHERE (r.rolsuper OR r.rolbypassrls)
            AND pg_has_role(current_user,r.oid,'MEMBER')) AS elevated_membership
        FROM pg_roles WHERE rolname=current_user"""))).mappings().one())
    rows = [dict(row) for row in (await connection.execute(text("""SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
        has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
        pg_has_role(current_user,c.relowner,'MEMBER') AS owns_table,
        (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid) AS policy_count,
        (SELECT pg_get_expr(p.polqual,p.polrelid) FROM pg_policy p WHERE p.polrelid=c.oid AND p.polname='institution_isolation') AS using_expression,
        (SELECT pg_get_expr(p.polwithcheck,p.polrelid) FROM pg_policy p WHERE p.polrelid=c.oid AND p.polname='institution_isolation') AS check_expression
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=:schema AND c.relkind IN ('r','p')"""), {"schema": schema})).mappings()]
    return role, rows


@asynccontextmanager
async def readonly_owner(engine, schema, institution_id, target):
    if schema not in OWNER_TABLES or not isinstance(institution_id, UUID) or institution_id.int == 0:
        raise ValueError("an explicit owner and nonzero institution UUID are required")
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            # First statement: database-enforced read-only, including for roles
            # that have INSERT privileges for their separate import responsibility.
            await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            actual = (await connection.execute(text("SELECT current_database(), current_setting('neon.project_id',true), current_setting('neon.branch_id',true)"))).one()
            if tuple(actual) != (target.database, target.project_id, target.branch_id):
                raise PersistenceConflict("read connection points to a different database target")
            await connection.execute(text("SELECT set_config('schedence.institution_id',:tenant,true)"), {"tenant": str(institution_id)})
            role, rows = await inspect_owner_security(connection, schema)
            check_read_security(role, rows, schema)
            yield connection, dict(schema=schema, role=role, tables=rows,
                legacy_staff_security_unchanged=schema == "academic")
        finally:
            if transaction.is_active:
                await transaction.rollback()


async def read_owner_tables(connection, schema, institution_id):
    # Identifiers come only from this fixed allowlist, never request text.
    result = {}
    for name in OWNER_TABLES[schema]:
        rows = (await connection.execute(text(f"SELECT * FROM {schema}.{name} WHERE institution_id=:tenant"),
            {"tenant": institution_id})).mappings()
        result[name] = tuple(sorted((dict(row) for row in rows), key=lambda row: str(row.get("id", row.get("occurrence_reference")))))
    return result


async def load_database_evidence(engines, institution_id, snapshot_id, target):
    """Detach verified source rows; all three DB transactions close before CP-SAT."""
    if set(engines) != set(OWNER_TABLES):
        raise ValueError("provide a restricted connection for each owning schema")
    security, owners = [], {}
    for schema in ("teaching", "academic", "resource"):
        async with readonly_owner(engines[schema], schema, institution_id, target) as (connection, audit):
            if schema == "teaching":
                session = AsyncSession(bind=connection, expire_on_commit=False)
                try:
                    document = await SqlTeachingRepository(session, institution_id).get_snapshot(snapshot_id)
                finally:
                    await session.close()
            owners[schema] = await read_owner_tables(connection, schema, institution_id)
            security.append(audit)
    evidence = DatabaseEvidence(institution_id, snapshot_id, document,
        owners["academic"], owners["resource"], owners["teaching"], tuple(security))
    verify_snapshot_children(evidence)
    return evidence


def verify_snapshot_children(evidence):
    document = evidence.document
    for table, rows, fields in (
        ("snapshot_sources", document.sources, ("label", "sha256", "owner", "provenance_kind", "provenance")),
        ("snapshot_scopes", document.scopes, tuple(type(document.scopes[0]).model_fields)),
        ("snapshot_issues", document.issues, ("issue_code", "severity", "scope_key", "details")),
    ):
        actual = [{key: row[key] for key in fields} for row in evidence.teaching[table] if row["snapshot_id"] == evidence.snapshot_id]
        expected = [{key: getattr(row, key) for key in fields} for row in rows]
        if sorted(map(canonical_json, actual)) != sorted(map(canonical_json, expected)):
            raise PersistenceConflict("normalized snapshot metadata differs from the immutable input document")


async def load_generation_scope(engines, target, request):
    """Application-service entry point returning detached validated solver inputs."""
    from .generation_inputs import DatabaseGenerationRequest, build_database_generation
    request = DatabaseGenerationRequest.model_validate(request.model_dump())
    evidence = await load_database_evidence(engines, request.institution_id, request.snapshot_id, target)
    return build_database_generation(evidence, request)
