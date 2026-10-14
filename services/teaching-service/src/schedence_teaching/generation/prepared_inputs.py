"""Isolated input snapshots with fresh READ ONLY owner evidence; no solve, import or queue acceptance."""

import asyncio
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import text

from schedence_teaching.api.errors import ApiError
from schedence_teaching.persistence.contracts import Sha256
from schedence_teaching.persistence.database_read import DatabaseEvidence, verify_snapshot_children
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.persistence.snapshots import content_digest
from schedence_teaching.schemas.models import InputContract, RequiredText

from .contracts import GenerationRequest
from .inputs import prepare_database_work
from .readiness import CALENDAR_SOURCE, POLICY_SOURCE, assess, load_owner_rows
from .scheduling_rules import rules_reference


class PreparedInputManifest(InputContract):
    schema_version: Literal[1] = 1
    institution_code: RequiredText
    revision: Annotated[int, Field(strict=True, ge=1)]
    request: GenerationRequest
    source_digest: Sha256
    validated_snapshot_id: UUID
    validated_digest: Sha256


def same_scope(request, other):
    return all(getattr(request, key) == getattr(other, key) for key in
        ("institution_id", "snapshot_id", "semester", "academic_years", "streams", "specializations"))


class IsolatedPreparedInputs:
    """The dedicated isolated repository supplies Teaching inputs; Academic/Resource stay read-only."""

    def __init__(self, repository, engines, target, manifest):
        from .persistence import IsolatedDraftPersistence
        IsolatedDraftPersistence(repository.engine.url)  # Reject a remote/non-isolated repository before any I/O.
        if set(engines) != {"academic", "resource"}:
            raise ValueError("prepared inputs require the two restricted owner readers")
        self.repository, self.engines, self.target = repository, engines, target
        self.manifest = PreparedInputManifest.model_validate(manifest.model_dump())
        if self.manifest.request.scheduling_rules is None:
            raise ValueError("a prepared version must record its explicitly selected scheduling rules")

    @classmethod
    def from_path(cls, repository, engines, target, path):
        return cls(repository, engines, target, PreparedInputManifest.model_validate_json(Path(path).read_text(encoding="utf-8")))

    async def _evidence(self):
        request = self.manifest.request
        async with self.repository.transaction(request.institution_id, read=True) as repo:
            document = await repo.get_snapshot(request.snapshot_id)
            if content_digest(document) != self.manifest.source_digest:
                raise PersistenceConflict("the prepared source snapshot differs from its immutable version")
            validated = await repo.get_snapshot(self.manifest.validated_snapshot_id)
            if content_digest(validated) != self.manifest.validated_digest:
                raise PersistenceConflict("the stored rule-bound snapshot differs from its validated version")
            children = {}
            for name in ("snapshot_sources", "snapshot_scopes", "snapshot_issues"):
                children[name] = tuple(dict(row) for row in (await repo._session.execute(text(
                    f"SELECT * FROM teaching.{name} WHERE institution_id=:tenant AND snapshot_id=:snapshot"),
                    {"tenant": request.institution_id, "snapshot": request.snapshot_id})).mappings())
        rows = await load_owner_rows(self.engines, request.institution_id, self.target)
        evidence = DatabaseEvidence(request.institution_id, request.snapshot_id, document,
            rows["academic"], rows["resource"], children)
        verify_snapshot_children(evidence)
        return evidence

    async def prepare(self, request):
        if request.scheduling_rules is None:
            raise ApiError(400, "SCHEDULING_RULES_REQUIRED", "Supply the explicitly selected Scheduling Rules for this prepared input version.")
        if not same_scope(request, self.manifest.request):
            raise ApiError(400, "PREPARED_SCOPE_MISMATCH", "Select the institution, input version and scope of this isolated preparation.")
        evidence = await self._evidence()
        work = await asyncio.to_thread(prepare_database_work, evidence, request)
        if (content_digest(request) == content_digest(self.manifest.request)
                and content_digest(work.document) != self.manifest.validated_digest):
            raise PersistenceConflict("worker reconstruction differs from the stored validated input version")
        return work

    async def assess(self, institution_id, scope, rules=None):
        request = self.manifest.request
        matches = (institution_id == request.institution_id and scope.semester == request.semester
            and scope.academic_years == request.academic_years and scope.streams == request.streams)
        if not matches:
            return dict(ready=False, checks=[dict(key="prepared-scope", title="Prepared input scope", status="MISSING",
                detail="This isolated input version is prepared for a different institution, year, semester or stream.")], inputs=None)
        evidence = await self._evidence()
        result = await asyncio.to_thread(assess, evidence.document,
            dict(academic=evidence.academic, resource=evidence.resource), institution_id, scope, rules,
            snapshot_id=request.snapshot_id, max_time_seconds=request.max_time_seconds,
            job_timeout_seconds=request.job_timeout_seconds)
        if result["ready"]:
            sources = {row.label: row.sha256 for row in evidence.document.sources}
            result["stored_input"] = dict(institution_id=institution_id, snapshot_id=request.snapshot_id,
                revision=self.manifest.revision, semantic_sha256=self.manifest.source_digest,
                validated_snapshot_id=self.manifest.validated_snapshot_id if rules == request.scheduling_rules else None,
                validated_rules_reference=rules_reference(request.scheduling_rules),
                semester=request.semester, academic_years=sorted(request.academic_years),
                streams=sorted(row.value for row in request.streams), calendar_label=evidence.document.calendar_label,
                academic_term_reference=evidence.document.academic_term_reference,
                calendar_reference=sources[CALENDAR_SOURCE], scheduling_policy_reference=sources[POLICY_SOURCE],
                persistence="ISOLATED_POSTGRESQL")
        return result
