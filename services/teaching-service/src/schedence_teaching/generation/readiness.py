"""Institution-scoped generation inputs and readiness. READ-ONLY: no snapshot storage, no solver.

An institution's input snapshot is prepared with the existing reviewed PP1 preparation
(`prepare_pp1.prepare_documents`) for THAT institution, bound to the institution's own owner
identifiers (staff, module source rows, venues), and then put through the generation worker's
own pre-solve path (`prepare_database_work`): owner-record reconstruction of staff, preferences,
modules, venues and allocations against the institution's restricted, RLS-scoped rows, scope
selection and calendar/policy reference checks. Any difference is a failed check, never a repair.

The scheduling configuration is the existing PP1 experimental calendar configuration. It is
labelled experimental and is never presented as university-approved.
"""

import asyncio
from dataclasses import dataclass
import threading
from uuid import UUID, uuid5

from schedence_teaching.persistence.contracts import ExternalReference, SnapshotDocument
from schedence_teaching.persistence.database_read import DatabaseEvidence, read_owner_tables, readonly_owner
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.persistence.snapshots import content_digest, replay_snapshot

from .contracts import GenerationRequest
from .inputs import prepare_database_work
from .scheduling_rules import EXPERIMENTAL_DEFAULTS, RulesError, check_objective_order, connected_rules, protected_rules, rules_reference

CONFIGURATION_LABEL = "EXPERIMENTAL_CONFIGURATION_NOT_UNIVERSITY_APPROVED"
CONFIGURATION_DESCRIPTION = ("PP1 experimental calendar configuration (existing reviewed PP1 settings and synthetic "
                             "reference week); for testing, not a university-approved scheduling policy.")
# Fixed namespace: prepared (unstored) snapshot identifiers are stable and never collide with database UUID4 values.
NAMESPACE = UUID("8b0e3f52-6d41-4c7a-9f1e-2a5c7d9e0b14")
CALENDAR_SOURCE = "PP1:calendar-configuration"
POLICY_SOURCE = "PP1:synthetic-elective-registration-evidence"


@dataclass(frozen=True)
class PreparationSources:
    registration_directory: object
    reviewed_directory: object
    settings_path: object
    timezone_name: str


@dataclass(frozen=True)
class ReadinessScope:
    semester: int
    academic_years: frozenset[int] = frozenset((1, 2, 3, 4))
    streams: frozenset[str] = frozenset(("WEEKDAY", "WEEKEND"))


def bind_owner_references(document, academic, resource):
    """Attach the institution's own owner identifiers; every staff member and venue must resolve exactly once."""
    def index(rows, column):
        result = {}
        for row in rows:
            if row[column] in result:
                raise PersistenceConflict(f"duplicate owner identity for {column}")
            result[row[column]] = row["id"]
        return result
    staff, modules, venues = index(academic["staff"], "staff_number"), index(academic["modules"], "record_key"), index(resource["venues"], "venue_code")
    references = []
    for member in document.teaching_input.staff_members:
        if member.staff_id not in staff:
            raise PersistenceConflict("a staff member has no owner record in this institution")
        references.append(ExternalReference(owner="ACADEMIC_DATA_SERVICE", record_kind="STAFF", local_reference=member.staff_id,
            authoritative_id=staff[member.staff_id], source_reference="academic.staff.staff_number"))
    references.extend(ExternalReference(owner="ACADEMIC_DATA_SERVICE", record_kind="MODULE_SOURCE_ROW", local_reference=key,
        authoritative_id=identifier, source_reference="academic.modules; not an authoritative offering identifier")
        for key, identifier in sorted(modules.items()))
    for venue in document.contexts.venue_input.venues:
        if venue.venue_id not in venues:
            raise PersistenceConflict("a venue has no owner record in this institution")
        references.append(ExternalReference(owner="RESOURCE_SERVICE", record_kind="VENUE", local_reference=venue.venue_id,
            authoritative_id=venues[venue.venue_id], source_reference="resource.venues.venue_code"))
    return SnapshotDocument.model_validate(document.model_dump() | dict(external_references=references))


class PreparedDocuments:
    """The reviewed preparation for one institution, computed once per process (it reads only local inputs)."""

    def __init__(self, sources):
        self.sources = sources
        self._cache, self._lock = {}, threading.Lock()

    def get(self, institution_id):
        with self._lock:
            if institution_id not in self._cache:
                from schedence_teaching.persistence.prepare_pp1 import prepare_documents
                documents, _ = prepare_documents(institution_id=institution_id,
                    registration_directory=self.sources.registration_directory, reviewed_directory=self.sources.reviewed_directory,
                    settings_path=self.sources.settings_path, timezone_name=self.sources.timezone_name)
                self._cache[institution_id] = documents
            return self._cache[institution_id]


async def load_owner_rows(engines, institution_id, target):
    """Academic and Resource rows of one institution, each through its restricted owner role in a READ ONLY transaction."""
    rows = {}
    for schema in ("academic", "resource"):
        async with readonly_owner(engines[schema], schema, institution_id, target) as (connection, _audit):
            rows[schema] = await read_owner_tables(connection, schema, institution_id)
    return rows


def _check(key, title, status, detail):
    return dict(key=key, title=title, status=status, detail=detail)


def assess(document, owner_rows, institution_id, scope, rules=None, *, snapshot_id=None, max_time_seconds=30.0, job_timeout_seconds=300.0):
    """The worker's pre-solve validation for one scope, without accepting a job or solving."""
    checks = []
    try:
        bound = bind_owner_references(document, owner_rows["academic"], owner_rows["resource"])
    except PersistenceConflict as error:
        return dict(ready=False, checks=[_check("owner-identifiers", "Owner identifiers", "MISSING", str(error))], inputs=None)
    checks.append(_check("owner-identifiers", "Owner identifiers", "READY",
        f"{len(bound.teaching_input.staff_members)} staff and {len(bound.contexts.venue_input.venues)} venues resolve to this institution's own records."))
    sources = {row.label: row for row in bound.sources}
    snapshot_id = snapshot_id or uuid5(NAMESPACE, f"{institution_id}:{content_digest(bound)}")
    request = GenerationRequest(institution_id=institution_id, snapshot_id=snapshot_id, semester=scope.semester,
        academic_years=scope.academic_years, streams=scope.streams, calendar_label=bound.calendar_label,
        policy_version="HISTORICAL_SNAPSHOT", academic_term_reference=bound.academic_term_reference,
        calendar_reference=sources[CALENDAR_SOURCE].sha256, scheduling_policy_reference=sources[POLICY_SOURCE].sha256,
        scheduling_rules=rules, max_time_seconds=max_time_seconds, job_timeout_seconds=job_timeout_seconds)
    evidence = DatabaseEvidence(institution_id, snapshot_id, bound, owner_rows["academic"], owner_rows["resource"], {})
    try:
        work = prepare_database_work(evidence, request)
        order = check_objective_order(work.document)
    except Exception as error:  # Any owner/scope/reference mismatch is reported, never repaired.
        message = getattr(error, "message", None) or str(error)
        rules_error = isinstance(error, RulesError) or getattr(error, "code", None) == "INVALID_SCHEDULING_RULES"
        checks.append(_check("scheduling-rules" if rules_error else "owner-records",
            "Scheduling rules" if rules_error else "Owner-record validation", "MISSING", message[:500]))
        return dict(ready=False, checks=checks, inputs=None)
    checks.append(_check("owner-records", "Owner-record validation", "READY",
        "Staff, lecturer preferences, modules, venues and student allocations match this institution's owner records exactly."))
    scoped = work.document
    prepared = replay_snapshot(scoped)[1]
    teaching = scoped.teaching_input
    supported = [row for row in scoped.scopes if row.scope_status == "SUPPORTED"]
    blocked = [dict(academic_year=row.academic_year, stream=row.stream.value, specialization=row.specialization,
                    student_count=row.student_count, scope_status=row.scope_status) for row in scoped.scopes if row.scope_status != "SUPPORTED"]
    expected = len(prepared.sessions)
    checks.append(_check("scope", "Selected scope", "MISSING" if not expected else "WARNING" if blocked else "READY",
        "No schedulable sessions in this scope." if not expected else
        f"{expected} sessions for {len(teaching.student_groups)} student groups"
        + (f"; not scheduled (missing academic mapping): {', '.join(f'Year {b['academic_year']} {b['specialization']} {b['stream'].lower()}' for b in blocked)}." if blocked else ".")))
    pending = sorted({row.issue_code for row in scoped.issues})
    if pending:
        checks.append(_check("issues", "Unresolved academic requirements", "WARNING",
            "Recorded and not scheduled: " + ", ".join(code.replace("_", " ").lower() for code in pending) + "."))
    checks.append(_check("configuration", "Scheduling configuration", "WARNING",
        f"{CONFIGURATION_DESCRIPTION} Calendar {scoped.calendar_label}, term {scoped.academic_term_reference}."))
    preferences = scoped.contexts.preference_input
    inputs = dict(
        staff=len(teaching.staff_members),
        modules=len({row.module_code for row in teaching.module_offerings}),
        module_offerings=len(teaching.module_offerings),
        student_groups=len(teaching.student_groups),
        venues=len(scoped.contexts.venue_input.venues),
        teaching_team_entries=len(teaching.teaching_team_members),
        lecturer_preferences=len(preferences.preferences) if preferences is not None else 0,
        session_requirements=len(teaching.session_requirements),
        expected_sessions=expected,
        specializations=sorted({row.specialization for row in supported}),
        blocked_scopes=blocked,
        unresolved_issue_codes=pending,
        calendar_label=scoped.calendar_label,
        academic_term_reference=scoped.academic_term_reference,
        configuration_label=CONFIGURATION_LABEL,
        prepared_snapshot_sha256=content_digest(bound),
    )
    ready = all(row["status"] != "MISSING" for row in checks)
    return dict(ready=ready, checks=checks, inputs=inputs, scheduling_rules=dict(
        selected=rules, experimental_defaults=EXPERIMENTAL_DEFAULTS,
        reference=rules_reference(rules) if rules is not None else None,
        connected=connected_rules(scoped), protected=protected_rules(scoped), objective_order=order))


class GenerationReadiness:
    """Read-only readiness for one institution and scope; owner rows are read fresh on every call."""

    def __init__(self, engines, target, prepared):
        if set(engines) != {"academic", "resource"}:
            raise ValueError("readiness needs the Academic and Resource restricted owner readers")
        self.engines, self.target, self.prepared = engines, target, prepared
        self.shutdown_hooks = ()
        self.stored_inputs = None

    async def assess(self, institution_id, scope, rules=None):
        if self.stored_inputs is not None:
            return await self.stored_inputs.assess(institution_id, scope, rules)
        documents = await asyncio.to_thread(self.prepared.get, institution_id)
        document = documents[scope.semester - 1]
        rows = await load_owner_rows(self.engines, institution_id, self.target)
        return await asyncio.to_thread(assess, document, rows, institution_id, scope, rules)
