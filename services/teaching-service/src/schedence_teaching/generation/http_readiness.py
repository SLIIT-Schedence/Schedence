"""Portable read-only readiness: owner HTTP exports plus Teaching-owned snapshots.

The required export endpoints are documented owner integration contracts, not
endpoints implemented in this service. Missing upstream APIs fail only readiness.
No private workbook fallback, database owner login, seed, snapshot write or solve.
"""

import asyncio
from uuid import UUID

from sqlalchemy import text

from schedence_teaching.api.errors import ApiError
from schedence_teaching.persistence.database_read import (OWNER_TABLES, DatabaseEvidence, check_read_security,
    inspect_owner_security)
from schedence_teaching.persistence.snapshots import content_digest, restore_snapshot

from .inputs import prepare_database_work
from .readiness import CALENDAR_SOURCE, POLICY_SOURCE, assess, bind_owner_references, _check

UUID_FIELDS = {"id", "institution_id", "source_id", "department_id", "specialization_id", "module_id",
               "allocation_id", "cohort_total_id", "parent_id", "identity_user_id"}


def owner_rows(payload, owner, institution):
    try:
        tables = payload["tables"]
        if payload["complete"] is not True or not isinstance(tables, dict) or set(tables) != set(OWNER_TABLES[owner]):
            raise ValueError("incomplete owner export")
        result = {}
        for name, rows in tables.items():
            if not isinstance(rows, list):
                raise ValueError("invalid owner collection")
            normalized, identifiers = [], set()
            for raw in rows:
                row = dict(raw)
                for field in UUID_FIELDS & row.keys():
                    if row[field] is not None:
                        row[field] = UUID(row[field])
                if (row.get("institution_id") != institution or row.get("id") is None or row["id"].int == 0
                        or row["id"] in identifiers):
                    raise ValueError("inconsistent owner row")
                identifiers.add(row["id"])
                normalized.append(row)
            result[name] = tuple(normalized)
        return result
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ApiError(503, "READINESS_UNAVAILABLE", "An owning service returned an incomplete or inconsistent input export.") from None


class DatabasePreparedDocuments:
    """Read an existing integrity-checked Teaching input snapshot, under its own RLS."""
    def __init__(self, factory):
        self.factory = factory

    async def find(self, institution, semester):
        """The latest stored snapshot covering this semester, as ``(snapshot_id, document)``, or None."""
        return await self._load(institution, """EXISTS(SELECT 1 FROM jsonb_array_elements(
            (payload->>'document_json')::jsonb->'scopes') scope WHERE (scope->>'semester')::int=:semester)""",
            {"semester": semester})

    async def get(self, institution, semester):
        found = await self.find(institution, semester)
        return found and found[1]

    async def by_id(self, institution, snapshot_id):
        found = await self._load(institution, "id=:snapshot", {"snapshot": snapshot_id})
        return found and found[1]

    async def _load(self, institution, condition, params):
        async with self.factory() as session:
            try:
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                await session.execute(text("SELECT set_config('schedence.institution_id',:tenant,true)"), {"tenant": str(institution)})
                role, tables = await inspect_owner_security(session, "teaching")
                check_read_security(role, tables, "teaching")
                # `condition` is one of the fixed fragments above, never request text.
                row = (await session.execute(text(f"""SELECT id,payload,content_sha256 FROM teaching.input_snapshots
                    WHERE institution_id=:tenant AND {condition}
                    ORDER BY created_at DESC,id DESC LIMIT 1"""), {"tenant": institution, **params})).first()
                if row is None:
                    return None
                if content_digest(row.payload) != row.content_sha256:
                    raise ValueError("Stored Teaching input checksum mismatch")
                document = restore_snapshot(row.payload)
                if document.institution_id != institution:
                    raise ValueError("Stored Teaching input institution mismatch")
                return row.id, document
            finally:
                await session.rollback()


async def owner_exports(owners, institution, bearer_token, semester, *, academic_path, resource_path):
    academic, resource = await asyncio.gather(
        owners.get("academic", academic_path, institution, bearer_token, params={"semester": semester}),
        owners.get("resource", resource_path, institution, bearer_token))
    return {"academic": owner_rows(academic, "academic", institution),
            "resource": owner_rows(resource, "resource", institution)}


def stored_snapshot_reference(snapshot_id, document):
    """What a generation request must quote to select this stored snapshot (checked again before solving)."""
    sources = {row.label: row for row in document.sources}
    return dict(snapshot_id=snapshot_id, academic_term_reference=document.academic_term_reference,
        calendar_label=document.calendar_label, calendar_reference=sources[CALENDAR_SOURCE].sha256,
        scheduling_policy_reference=sources[POLICY_SOURCE].sha256)


class ApiGenerationReadiness:
    requires_bearer_token = True

    def __init__(self, owners, prepared, *, academic_path, resource_path):
        self.owners, self.prepared = owners, prepared
        self.academic_path, self.resource_path = academic_path, resource_path
        self.shutdown_hooks = ()

    async def assess(self, institution, scope, rules=None, *, bearer_token):
        rows = await owner_exports(self.owners, institution, bearer_token, scope.semester,
            academic_path=self.academic_path, resource_path=self.resource_path)
        records = owner_record_counts(rows)
        found = await self.prepared.find(institution, scope.semester)
        if found is None:
            return dict(ready=False, inputs=None, owner_records=records, checks=[_check("prepared-input",
                "Prepared Teaching inputs", "MISSING",
                "No validated Teaching input snapshot exists for this institution and semester. "
                "An approved calendar, teaching requirements and student allocations must be prepared first.")])
        snapshot_id, document = found
        result = await asyncio.to_thread(assess, document, rows, institution, scope, rules, snapshot_id=snapshot_id)
        return {**result, "owner_records": records, "prepared_snapshot": stored_snapshot_reference(snapshot_id, document)}


class OwnerApiGenerationInputs:
    """Generation inputs from the owners' authorized HTTP exports, read with the submitting caller's token.

    The same path readiness uses: the stored Teaching snapshot is re-bound to this institution's own owner
    records and validated before any job is accepted. No Academic/Resource database login is involved.
    """
    requires_bearer_token = True

    def __init__(self, owners, prepared, *, academic_path, resource_path):
        self.owners, self.prepared = owners, prepared
        self.academic_path, self.resource_path = academic_path, resource_path

    async def prepare(self, request, *, bearer_token):
        institution = request.institution_id
        rows = await owner_exports(self.owners, institution, bearer_token, request.semester,
            academic_path=self.academic_path, resource_path=self.resource_path)
        document = await self.prepared.by_id(institution, request.snapshot_id)
        if document is None:
            raise ApiError(400, "INVALID_GENERATION_INPUTS", "The selected input snapshot does not exist in this institution.")
        bound = bind_owner_references(document, rows["academic"], rows["resource"])
        evidence = DatabaseEvidence(institution, request.snapshot_id, bound, rows["academic"], rows["resource"], {})
        return await asyncio.to_thread(prepare_database_work, evidence, request)


def owner_record_counts(rows):
    """Institution-wide owner totals; unlike assess() inputs these are neither scoped nor validated."""
    academic = rows["academic"]
    return dict(staff=len(academic["staff"]), modules=len(academic["modules"]),
        student_groups=len(academic["pp1_student_groups"]), teaching_team_entries=len(academic["module_teaching_teams"]),
        venues=len(rows["resource"]["venues"]), lecturer_preferences=len(academic["lecturer_preferences"]))
