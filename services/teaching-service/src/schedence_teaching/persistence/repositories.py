"""Tenant-bound repository ports and an opt-in SQLAlchemy implementation.

Callers supply a shared db-core session factory explicitly. Importing this module
does not read environment configuration, create an engine, or connect to Neon.
"""

from contextlib import asynccontextmanager
from datetime import date
from hashlib import sha256
import json
from typing import AsyncIterator
from uuid import UUID, uuid4

from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from schedence_teaching.solver import SolverResult

from .contracts import SnapshotDocument
from .models import (
    Delivery, DeliveryCoverage, GenerationRun, InputSnapshot, SnapshotIssue,
    SnapshotScope, SnapshotSource, TimetableSeries, TimetableVersion, ValidationReport,
)
from .snapshots import canonical_json, content_digest, prepare_attempt, replay_snapshot, restore_snapshot, snapshot_payload
from .ports import PersistenceConflict, StoredAttempt, VersionSummary


def _tenant(value: UUID) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError("an explicit nonzero institution UUID is required")
    return value


class SqlTeachingRepository:
    """Append-only operations; commit/rollback belongs to the surrounding UoW."""

    def __init__(self, session: AsyncSession, institution_id: UUID):
        self._session = session
        self.institution_id = _tenant(institution_id)

    async def store_snapshot(self, document: SnapshotDocument) -> UUID:
        snapshot, prepared, _, _ = replay_snapshot(document)
        if snapshot.institution_id != self.institution_id:
            raise PersistenceConflict("snapshot belongs to a different institution")
        # Serialize cooperating importers for this institution. Compare semantic
        # inputs, not JSON text ordering: old report replay still needs exact text.
        lock_key = int.from_bytes(sha256(self.institution_id.bytes + b":teaching-input-import").digest()[:8],
                                  byteorder="big", signed=True)
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))
        candidates = await self._session.scalars(select(InputSnapshot).where(
            InputSnapshot.institution_id == self.institution_id,
            InputSnapshot.academic_term_reference == snapshot.academic_term_reference,
            InputSnapshot.calendar_label == snapshot.calendar_label,
            InputSnapshot.expected_occurrences == len(prepared.sessions)))
        fingerprint = content_digest(snapshot)
        for candidate in candidates.all():
            if content_digest(candidate.payload) != candidate.content_sha256:
                raise PersistenceConflict("stored snapshot integrity check failed")
            existing = restore_snapshot(candidate.payload)
            if existing.institution_id != self.institution_id:
                raise PersistenceConflict("stored snapshot belongs to a different institution")
            if content_digest(existing) == fingerprint:
                return candidate.id
        snapshot_id = uuid4()
        tenant = self.institution_id
        payload = snapshot_payload(snapshot)
        self._session.add(InputSnapshot(id=snapshot_id, institution_id=tenant,
            schema_version=snapshot.schema_version, content_sha256=content_digest(payload),
            academic_term_reference=snapshot.academic_term_reference, timezone_name=snapshot.timezone_name,
            calendar_label=snapshot.calendar_label, expected_occurrences=len(prepared.sessions),
            payload=payload))
        await self._session.flush()
        for source in snapshot.sources:
            self._session.add(SnapshotSource(id=uuid4(), institution_id=tenant, snapshot_id=snapshot_id,
                label=source.label, sha256=source.sha256, owner=source.owner.value,
                provenance_kind=source.provenance_kind.value, provenance=json.loads(canonical_json(source.provenance))))
        for scope in snapshot.scopes:
            self._session.add(SnapshotScope(id=uuid4(), institution_id=tenant, snapshot_id=snapshot_id,
                **json.loads(canonical_json(scope))))
        await self._session.flush()
        for issue in snapshot.issues:
            self._session.add(SnapshotIssue(id=uuid4(), institution_id=tenant, snapshot_id=snapshot_id,
                **json.loads(canonical_json(issue))))
        await self._session.flush()
        return snapshot_id

    async def get_snapshot(self, snapshot_id: UUID) -> SnapshotDocument:
        row = await self._session.scalar(select(InputSnapshot).where(
            InputSnapshot.institution_id == self.institution_id, InputSnapshot.id == snapshot_id))
        if row is None:
            raise PersistenceConflict("snapshot not found in this institution")
        if content_digest(row.payload) != row.content_sha256:
            raise PersistenceConflict("stored snapshot integrity check failed")
        document, prepared, _, _ = replay_snapshot(restore_snapshot(row.payload))
        if (document.institution_id != self.institution_id
                or len(prepared.sessions) != row.expected_occurrences):
            raise PersistenceConflict("stored snapshot integrity check failed")
        return document

    async def create_series(self, reference: str) -> UUID:
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("a series reference is required")
        series_id = uuid4()
        self._session.add(TimetableSeries(id=series_id, institution_id=self.institution_id, series_reference=reference))
        await self._session.flush()
        return series_id

    async def list_versions(self, series_id: UUID) -> tuple[VersionSummary, ...]:
        result = await self._session.scalars(select(TimetableVersion).where(
            TimetableVersion.institution_id == self.institution_id,
            TimetableVersion.series_id == series_id).order_by(TimetableVersion.version_number))
        return tuple(VersionSummary(**{name: getattr(row, name) for name in VersionSummary.__dataclass_fields__})
                     for row in result.all())

    async def append_attempt(self, series_id: UUID, snapshot_id: UUID, result: SolverResult, *,
        expected_previous_version_id: UUID | None,
        created_by_identity_user_id: UUID | None = None,
        persistence_metadata: dict | None = None) -> StoredAttempt:
        document = await self.get_snapshot(snapshot_id)
        plan = prepare_attempt(document, result)
        if document.solver_config.resolved_policy is not None:
            from .policy import verify_snapshot_policy
            from .versioning import delivery_digest
            policy = verify_snapshot_policy(document)
            provided = dict(persistence_metadata or {})
            if provided.get('resolved_policy_digest', policy.digest) != policy.digest:
                raise PersistenceConflict('Requested version policy digest differs from its snapshot.')
            persistence_metadata = dict(provided)
            integrity = dict(snapshot_sha256=plan.snapshot_digest,
                normalized_deliveries_sha256=delivery_digest(json.loads(plan.deliveries_json)),
                institution_policy_reference=policy.policy_reference, institution_policy_revision=policy.revision)
            if any(key in provided and provided[key] != value for key, value in integrity.items()):
                raise PersistenceConflict('Requested version provenance differs from its validated snapshot.')
            persistence_metadata.update(integrity)
            persistence_metadata.setdefault('logical_status', 'VALIDATED')
            persistence_metadata.update(resolved_policy_digest=policy.digest,
                institution_policy_reference=policy.policy_reference, institution_policy_revision=policy.revision)
        # Serialize version allocation per series and reject stale callers. This
        # lock changes no row and holds until the shared UoW ends.
        series = await self._session.scalar(select(TimetableSeries).where(
            TimetableSeries.institution_id == self.institution_id,
            TimetableSeries.id == series_id).with_for_update())
        if series is None:
            raise PersistenceConflict("series not found in this institution")
        latest = await self._session.scalar(select(TimetableVersion).where(
            TimetableVersion.institution_id == self.institution_id,
            TimetableVersion.series_id == series_id).order_by(TimetableVersion.version_number.desc()).limit(1))
        if (latest.id if latest is not None else None) != expected_previous_version_id:
            raise PersistenceConflict("series changed; review the latest version before appending")
        tenant, run_id, report_id = self.institution_id, uuid4(), uuid4()
        result_metadata = json.loads(plan.result_json)
        validation_evidence = json.loads(plan.validation_json)
        if persistence_metadata is not None:
            from .versioning import METADATA_KEY
            result_metadata[METADATA_KEY] = json.loads(canonical_json(persistence_metadata))
            validation_evidence[METADATA_KEY] = result_metadata[METADATA_KEY]
        self._session.add(GenerationRun(id=run_id, institution_id=tenant, series_id=series_id,
            snapshot_id=snapshot_id, solver_status=plan.solver_status, solver_success=plan.success,
            expected_occurrences=plan.expected_occurrences, scheduled_occurrences=plan.scheduled_occurrences,
            runtime_seconds=plan.runtime_seconds, result_metadata=result_metadata))
        await self._session.flush()
        self._session.add(ValidationReport(id=report_id, institution_id=tenant, run_id=run_id,
            expected_occurrences=plan.expected_occurrences, checked_occurrences=plan.scheduled_occurrences,
            hard_violations=0 if plan.success else None, passed=plan.success, evidence=validation_evidence))
        await self._session.flush()
        if not plan.success:
            return StoredAttempt(run_id, report_id, None, None)
        version_id = uuid4()
        number = latest.version_number + 1 if latest is not None else 1
        self._session.add(TimetableVersion(id=version_id, institution_id=tenant, series_id=series_id,
            run_id=run_id, validation_report_id=report_id, validation_passed=True, solver_success=True,
            version_number=number, previous_version_id=expected_previous_version_id,
            lifecycle_status="DRAFT_VALIDATED", scope_completion="COMPLETE_SUPPORTED_SCOPE",
            # Known teaching scope completion is not proof of academic completion,
            # Research fulfillment, publication approval, or a resource reservation.
            academic_complete=False, created_by_identity_user_id=created_by_identity_user_id))
        await self._session.flush()
        coverage_rows = []
        for row in json.loads(plan.deliveries_json):
            coverage = row.pop("coverage")
            delivery_id = uuid4()
            row["teaching_date"] = date.fromisoformat(row["teaching_date"])
            self._session.add(Delivery(id=delivery_id, institution_id=tenant, version_id=version_id, **row))
            for occurrence in coverage:
                coverage_rows.append(DeliveryCoverage(institution_id=tenant, version_id=version_id,
                                                      delivery_id=delivery_id, **occurrence))
        await self._session.flush()
        for occurrence in coverage_rows:
            self._session.add(occurrence)
        await self._session.flush()
        return StoredAttempt(run_id, report_id, version_id, number)

    async def save_validated_result(self, series_id, snapshot_id, result, *, expected_previous_version_id,
                                    logical_status="VALIDATED", created_by_identity_user_id=None):
        """Idempotent safe save using the existing append path and per-series lock.

        A stale identical retry reuses its original version, even after a newer
        version exists. A genuinely different stale save must be reviewed/retried.
        """
        from .lifecycle import academic_limitations, draft_status
        from .versioning import METADATA_KEY, delivery_digest, run_identity
        status = draft_status(logical_status)
        document = await self.get_snapshot(snapshot_id)
        plan = prepare_attempt(document, result)
        if not plan.success:
            raise PersistenceConflict("only a complete independently validated result may be saved as a draft")
        series = await self._session.scalar(select(TimetableSeries).where(
            TimetableSeries.institution_id == self.institution_id, TimetableSeries.id == series_id).with_for_update())
        if series is None:
            raise PersistenceConflict("series not found in this institution")
        identity = run_identity(plan.snapshot_digest, json.loads(plan.result_json), status.value)
        candidates = (await self._session.execute(select(GenerationRun, TimetableVersion).join(TimetableVersion,
            (TimetableVersion.institution_id == GenerationRun.institution_id) & (TimetableVersion.run_id == GenerationRun.id))
            .where(GenerationRun.institution_id == self.institution_id, GenerationRun.series_id == series_id,
                GenerationRun.snapshot_id == snapshot_id, TimetableVersion.institution_id == self.institution_id,
                TimetableVersion.series_id == series_id).order_by(TimetableVersion.version_number))).all()
        for run, version in candidates:
            stored = run.result_metadata.get(METADATA_KEY, {})
            existing_status = stored.get("logical_status", "VALIDATED")
            # Also recognizes legacy drafts, without adding a fingerprint to them.
            if run_identity(plan.snapshot_digest, run.result_metadata, existing_status) == identity:
                await self.get_timetable(version.id)
                return StoredAttempt(run.id, version.validation_report_id, version.id, version.version_number, True)
        metadata = dict(format_version=1, run_sha256=identity, snapshot_sha256=plan.snapshot_digest,
            logical_status=status.value, storage_status="DRAFT_VALIDATED",
            normalized_deliveries_sha256=delivery_digest(json.loads(plan.deliveries_json)),
            limitations=academic_limitations(document), calendar_configuration_reference=dict(
                snapshot_id=str(snapshot_id), calendar_label=document.calendar_label,
                calendar_sha256=content_digest(document.calendar), solver_rules_sha256=content_digest(document.solver_config)),
            sources=[row.model_dump(mode="json") for row in document.sources],
            scope=[row.model_dump(mode="json") for row in document.scopes],
            objective={key: json.loads(plan.result_json).get(key) for key in ("objective_mode", "objective_value", "objective_status")})
        if document.solver_config.resolved_policy is not None:
            from .policy import verify_snapshot_policy
            policy = verify_snapshot_policy(document)
            metadata.update(resolved_policy_digest=policy.digest, institution_policy_revision=policy.revision,
                            institution_policy_reference=policy.policy_reference)
        receipt = await self.append_attempt(series_id, snapshot_id, result,
            expected_previous_version_id=expected_previous_version_id,
            created_by_identity_user_id=created_by_identity_user_id, persistence_metadata=metadata)
        await self.get_timetable(receipt.version_id)
        return receipt

    async def get_series(self, series_id):
        row = await self._session.scalar(select(TimetableSeries).where(
            TimetableSeries.institution_id == self.institution_id, TimetableSeries.id == series_id))
        if row is None:
            raise PersistenceConflict("series not found in this institution")
        return dict(id=row.id, institution_id=row.institution_id, series_reference=row.series_reference,
            created_at=row.created_at, versions=await self.list_versions(series_id))

    async def get_latest_draft(self, series_id):
        await self.get_series(series_id)
        identifier = await self._session.scalar(select(TimetableVersion.id).where(
            TimetableVersion.institution_id == self.institution_id, TimetableVersion.series_id == series_id)
            .order_by(TimetableVersion.version_number.desc()).limit(1))
        return None if identifier is None else await self.get_timetable(identifier)

    async def get_version(self, series_id, version_number):
        if type(version_number) is not int or version_number < 1:
            raise ValueError("version number must be a positive integer")
        identifier = await self._session.scalar(select(TimetableVersion.id).where(
            TimetableVersion.institution_id == self.institution_id, TimetableVersion.series_id == series_id,
            TimetableVersion.version_number == version_number))
        if identifier is None:
            raise PersistenceConflict("version not found in this institution and series")
        return await self.get_timetable(identifier)

    async def get_timetable(self, version_id):
        """Detach complete original coverage and provenance; no lazy SQL escapes."""
        from .retrieval import assemble_timetable
        version = await self._session.scalar(select(TimetableVersion).where(
            TimetableVersion.institution_id == self.institution_id, TimetableVersion.id == version_id))
        if version is None:
            raise PersistenceConflict("version not found in this institution")
        run = await self._session.scalar(select(GenerationRun).where(
            GenerationRun.institution_id == self.institution_id, GenerationRun.id == version.run_id,
            GenerationRun.series_id == version.series_id))
        report = await self._session.scalar(select(ValidationReport).where(
            ValidationReport.institution_id == self.institution_id, ValidationReport.id == version.validation_report_id,
            ValidationReport.run_id == version.run_id))
        if run is None or report is None:
            raise PersistenceConflict("version has missing run or validation evidence")
        document = await self.get_snapshot(run.snapshot_id)
        deliveries = (await self._session.scalars(select(Delivery).where(
            Delivery.institution_id == self.institution_id, Delivery.version_id == version_id)
            .order_by(Delivery.teaching_date, Delivery.start_minute, Delivery.delivery_reference))).all()
        coverage = (await self._session.scalars(select(DeliveryCoverage).where(
            DeliveryCoverage.institution_id == self.institution_id, DeliveryCoverage.version_id == version_id)
            .order_by(DeliveryCoverage.occurrence_reference))).all()
        def values(row):
            return {column.name: getattr(row, column.name) for column in row.__table__.columns}
        return assemble_timetable(document, values(version), values(run), values(report),
            tuple(values(row) for row in deliveries), tuple(values(row) for row in coverage))

    async def find_timetables(self, *, offset=0, limit=20, series_id=None, years=(), semesters=(), streams=(),
                              specializations=(), groups=(), lecturer=None, venue=None, lifecycle_status=None):
        """Bounded, tenant-filtered read query; filters never contain SQL identifiers."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("bounded pagination is required")
        tenant = self.institution_id
        query = select(TimetableVersion.id).join(GenerationRun,
            (GenerationRun.id == TimetableVersion.run_id) & (GenerationRun.institution_id == TimetableVersion.institution_id))
        query = query.where(TimetableVersion.institution_id == tenant, GenerationRun.institution_id == tenant)
        if series_id is not None:
            query = query.where(TimetableVersion.series_id == series_id, GenerationRun.series_id == series_id)
        period = [("academic_year", years), ("semester", semesters), ("stream", streams), ("specialization", specializations)]
        scope = select(SnapshotScope.id).where(SnapshotScope.institution_id == tenant,
            SnapshotScope.snapshot_id == GenerationRun.snapshot_id)
        for field, values in period:
            if values:
                scope = scope.where(getattr(SnapshotScope, field).in_(values))
        if groups:
            scope = scope.where(or_(*(SnapshotScope.group_references.contains([group]) for group in groups)))
        query = query.where(scope.exists())
        if lecturer is not None or venue is not None:
            booking = select(Delivery.id).join(DeliveryCoverage,
                (DeliveryCoverage.delivery_id == Delivery.id) & (DeliveryCoverage.version_id == Delivery.version_id)
                & (DeliveryCoverage.institution_id == Delivery.institution_id)).where(
                    Delivery.institution_id == tenant, DeliveryCoverage.institution_id == tenant,
                    Delivery.version_id == TimetableVersion.id)
            if lecturer is not None:
                booking = booking.where(Delivery.staff_reference == lecturer)
            if venue is not None:
                booking = booking.where(Delivery.venue_reference == venue)
            for field, values in period:
                if values:
                    booking = booking.where(getattr(DeliveryCoverage, field).in_(values))
            query = query.where(booking.exists())
        if lifecycle_status is not None:
            if lifecycle_status in {"DRAFT", "VALIDATED"}:
                from .versioning import METADATA_KEY
                query = query.where(TimetableVersion.lifecycle_status == "DRAFT_VALIDATED",
                    func.coalesce(GenerationRun.result_metadata[METADATA_KEY]["logical_status"].astext, "VALIDATED") == lifecycle_status)
            else:
                # Never relabel a historical draft as published.
                query = query.where(TimetableVersion.lifecycle_status == lifecycle_status)
        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        ordering = (TimetableVersion.version_number.desc(), TimetableVersion.id) if series_id is not None else (
            TimetableVersion.created_at.desc(), TimetableVersion.id)
        identifiers = (await self._session.scalars(query.order_by(*ordering).offset(offset).limit(limit))).all()
        return tuple([await self.get_timetable(identifier) for identifier in identifiers]), total


@asynccontextmanager
async def teaching_unit_of_work(factory: async_sessionmaker[AsyncSession], institution_id: UUID) -> AsyncIterator[SqlTeachingRepository]:
    """Explicit opt-in only; reuse db-core transaction ownership and rollback.

    Transaction-local tenant context is safe with pooled connections. RLS still
    requires a production role without superuser/BYPASSRLS privileges.
    """
    from schedence_db import session_scope

    tenant = _tenant(institution_id)
    async with session_scope(factory, commit=True) as session:
        await session.execute(select(func.set_config("schedence.institution_id", str(tenant), True)))
        yield SqlTeachingRepository(session, tenant)
