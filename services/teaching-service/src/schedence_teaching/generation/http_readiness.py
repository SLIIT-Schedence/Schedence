"""Portable read-only readiness: owner HTTP exports plus Teaching-owned snapshots.

The required export endpoints are documented owner integration contracts, not
endpoints implemented in this service. Missing upstream APIs fail only readiness.
No private workbook fallback, database owner login, seed, snapshot write or solve.
"""

import asyncio
from uuid import UUID

from sqlalchemy import text

from schedence_teaching.api.errors import ApiError
from schedence_teaching.persistence.database_read import OWNER_TABLES, check_read_security, inspect_owner_security
from schedence_teaching.persistence.snapshots import content_digest, restore_snapshot

from .readiness import assess, _check

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

    async def get(self, institution, semester):
        async with self.factory() as session:
            try:
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                await session.execute(text("SELECT set_config('schedence.institution_id',:tenant,true)"), {"tenant": str(institution)})
                role, tables = await inspect_owner_security(session, "teaching")
                check_read_security(role, tables, "teaching")
                row = (await session.execute(text("""SELECT payload,content_sha256 FROM teaching.input_snapshots
                    WHERE institution_id=:tenant AND EXISTS(SELECT 1 FROM jsonb_array_elements(
                        (payload->>'document_json')::jsonb->'scopes') scope
                        WHERE (scope->>'semester')::int=:semester)
                    ORDER BY created_at DESC,id DESC LIMIT 1"""), {"tenant": institution, "semester": semester})).first()
                if row is None:
                    return None
                if content_digest(row.payload) != row.content_sha256:
                    raise ValueError("Stored Teaching input checksum mismatch")
                document = restore_snapshot(row.payload)
                if document.institution_id != institution:
                    raise ValueError("Stored Teaching input institution mismatch")
                return document
            finally:
                await session.rollback()


class ApiGenerationReadiness:
    requires_bearer_token = True

    def __init__(self, owners, prepared, *, academic_path, resource_path):
        self.owners, self.prepared = owners, prepared
        self.academic_path, self.resource_path = academic_path, resource_path
        self.shutdown_hooks = ()

    async def assess(self, institution, scope, rules=None, *, bearer_token):
        academic, resource = await asyncio.gather(
            self.owners.get("academic", self.academic_path, institution, bearer_token, params={"semester": scope.semester}),
            self.owners.get("resource", self.resource_path, institution, bearer_token))
        rows = {"academic": owner_rows(academic, "academic", institution),
                "resource": owner_rows(resource, "resource", institution)}
        document = await self.prepared.get(institution, scope.semester)
        if document is None:
            return dict(ready=False, inputs=None, checks=[_check("prepared-input", "Prepared Teaching inputs", "MISSING",
                "No validated Teaching input snapshot exists for this institution and semester. "
                "An approved calendar, teaching requirements and student allocations must be prepared first.")])
        return await asyncio.to_thread(assess, document, rows, institution, scope, rules)
