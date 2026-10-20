"""Service layer of the Fairness-Aware Staff Allocation for Examination Duties API: explicit data source, cached snapshot, preview runs.

* One explicit source per deployment (``M3_DEMO_SOURCE``); there is never a fallback from
  NEON_READ_ONLY to local data. Every result says which source produced it.
* The snapshot (``M3DemoRecords``) is read-only and cached for ``snapshot_ttl_seconds``.
* Allocation, swap, replacement and seating results are PREVIEWS held in a small in-memory
  store (lost on restart, per process). Nothing is saved, approved or published.
* The synthetic ``m3_demo`` institution is never merged with the real examination
  institution: if both ids were equal the service refuses to run.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any

from app.duty_allocation.demo_database import (
    DemoDatabaseError, DemoDatabaseUnavailable, DemoSchemaError, DemoTargetRefused, M3DemoRecords, ReadOnlyViolation,
    RowSource, load_m3_demo_records,
)
from app.duty_allocation.demo_integration import (
    DemoAllocationResult, DemoSeatingResult, M3DemoInputs, build_demo_inputs, run_demo_duty_allocation, run_demo_seating,
)
from app.duty_allocation.demo_local import CsvRowSource
from app.duty_allocation.demo_mapper import DemoDataError
from app.duty_allocation.demo_production import DemoRoleRefused, production_read_only_m3_demo
from app.duty_allocation.demo_settings import DemoSource, Member3DemoSettings
from app.duty_allocation.duty_changes import ChangeProposal, check_proposal, propose_replacement, propose_swap
from app.duty_allocation.fairness_policy import FairnessInputs
from app.duty_allocation.orchestration import AllocationMode, OrchestrationResult
from app.duty_allocation.types import DutyAssignment
from app.duty_allocation.validation import validate_allocation
from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling.policy import SolverSettings

DEMO_REFERENCE_PREFIX = "DEMO-UNPUBLISHED:m3_demo/"
MAX_STORED_RUNS = 25
SAFEGUARDS = (
    "Preview only: nothing is saved, approved, published or applied.",
    "Read-only data access (SELECT only, REPEATABLE READ, always rolled back).",
    "Synthetic m3_demo institution; never merged with the real examination institution.",
    "NEON_READ_ONLY never falls back to local data.",
)


class DemoServiceError(Exception):
    """A safe, credential-free error with an HTTP status and a stable code."""

    def __init__(self, status_code: int, code: str, message: str, problems: tuple[str, ...] = ()) -> None:
        self.status_code, self.code, self.message, self.problems = status_code, code, message, problems
        super().__init__(message)


@dataclass(frozen=True)
class Snapshot:
    records: M3DemoRecords
    data_source: DemoSource
    source_description: str
    loaded_at: dt.datetime


@dataclass(frozen=True)
class DemoContext:
    snapshot: Snapshot
    inputs: M3DemoInputs
    period_code: str
    institution_id: uuid.UUID


@dataclass(frozen=True)
class StoredAllocation:
    run_id: str
    created_at: dt.datetime
    context: DemoContext
    mode: AllocationMode
    result: DemoAllocationResult

    @property
    def orchestration(self) -> OrchestrationResult:
        return self.result.orchestration

    def roster(self, label: str) -> tuple[DutyAssignment, ...] | None:
        chosen = {"BASELINE": self.orchestration.baseline, "FAIR": self.orchestration.fair}.get(label)
        return None if chosen is None else chosen.assignments


@dataclass(frozen=True)
class StoredSeating:
    seating_id: str
    created_at: dt.datetime
    context: DemoContext
    result: DemoSeatingResult


class PreviewStore:
    """Bounded in-memory store: the oldest preview is dropped first. Not persistence."""

    def __init__(self, limit: int = MAX_STORED_RUNS) -> None:
        self._items: OrderedDict[str, Any] = OrderedDict()
        self._limit = limit

    def add(self, key: str, item: Any) -> None:
        self._items[key] = item
        while len(self._items) > self._limit:
            self._items.popitem(last=False)

    def get(self, key: str, kind: type) -> Any:
        item = self._items.get(key)
        if not isinstance(item, kind):
            raise DemoServiceError(404, "M3_PREVIEW_NOT_FOUND",
                                   "No such preview. Previews are kept in memory only and may have expired; "
                                   "generate it again.")
        return item


RowSourceFactory = Callable[[Member3DemoSettings], AbstractAsyncContextManager[RowSource]]


@asynccontextmanager
async def default_row_source(settings: Member3DemoSettings) -> AsyncIterator[RowSource]:
    """LOCAL_DEMO -> seed-pack files; NEON_READ_ONLY -> the guarded production read-only reader."""
    if settings.source is DemoSource.LOCAL_DEMO:
        yield CsvRowSource(settings.seed_dir)
        return
    async with production_read_only_m3_demo(
            settings.neon_env_file, url_variable=settings.neon_url_variable, expected_host=settings.neon_host,
            expected_database=settings.neon_database, expected_branch=settings.neon_branch,
            expected_role=settings.neon_role, confirmed_in_neon_console=settings.neon_confirmed_in_console,
            credential_rotated=settings.neon_credential_rotated, acknowledgement=settings.neon_acknowledgement) as source:
        yield source


class Member3DemoService:
    def __init__(self, settings: Member3DemoSettings, *, app_env: str, real_institution_id: uuid.UUID,
                 row_source: RowSourceFactory = default_row_source, clock: Callable[[], float] = time.monotonic) -> None:
        self.settings, self.app_env, self.real_institution_id = settings, app_env, real_institution_id
        self._row_source, self._clock = row_source, clock
        self._snapshot: Snapshot | None = None
        self._loaded_at_tick = 0.0
        self._inputs: dict[str, M3DemoInputs] = {}
        self._lock = asyncio.Lock()
        self.store = PreviewStore()

    # --- Source gating -----------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        s = self.settings
        problem = self._gate_problem()
        return {"source": s.source, "enabled": problem is None, "problem": problem,
                "missing_settings": s.missing_setup(), "institution_id": s.institution_id,
                "default_period_code": s.period_code, "app_env": self.app_env, "safeguards": SAFEGUARDS,
                "snapshot_loaded": self._snapshot is not None}

    def _gate_problem(self) -> tuple[int, str, str] | None:
        s = self.settings
        if s.source is DemoSource.DISABLED:
            return 503, "M3_DEMO_DISABLED", "The Fairness-Aware Staff Allocation for Examination Duties demo source is disabled. Set M3_DEMO_SOURCE to enable it."
        if s.institution_id == self.real_institution_id:
            return (409, "M3_DEMO_INSTITUTION_CONFLICT",
                    "The synthetic demo institution must differ from the real examination institution.")
        if s.source is DemoSource.LOCAL_DEMO and self.app_env == "production":
            return 403, "M3_DEMO_LOCAL_FORBIDDEN", "The local demo source is not available in production."
        missing = s.missing_setup()
        if missing:
            return (503, "M3_DEMO_NOT_CONFIGURED",
                    "The selected data source is not configured. Missing: " + ", ".join(missing) + ".")
        return None

    def _require_enabled(self) -> None:
        problem = self._gate_problem()
        if problem is not None:
            raise DemoServiceError(*problem)

    # --- Snapshot and inputs -----------------------------------------------------------------------

    async def snapshot(self, *, refresh: bool = False) -> Snapshot:
        self._require_enabled()
        async with self._lock:
            fresh = (self._snapshot is not None and not refresh
                     and self._clock() - self._loaded_at_tick < self.settings.snapshot_ttl_seconds)
            if fresh:
                return self._snapshot
            try:
                async with self._row_source(self.settings) as source:
                    records = await load_m3_demo_records(source)
            except DemoServiceError:
                raise
            except (DemoTargetRefused, DemoRoleRefused) as exc:
                raise DemoServiceError(403, exc.code, str(exc)) from None
            except DemoDatabaseUnavailable as exc:
                raise DemoServiceError(503, exc.code, str(exc)) from None
            except DemoSchemaError as exc:
                raise DemoServiceError(502, exc.code, str(exc)) from None
            except (ReadOnlyViolation, DemoDatabaseError) as exc:
                raise DemoServiceError(500, exc.code, str(exc)) from None
            self._snapshot = Snapshot(records, self.settings.source, self._describe(), dt.datetime.now(dt.timezone.utc))
            self._loaded_at_tick = self._clock()
            self._inputs.clear()
            return self._snapshot

    def _describe(self) -> str:
        if self.settings.source is DemoSource.LOCAL_DEMO:
            return "Local synthetic seed pack (no database)"
        return "Neon production branch, schema m3_demo, dedicated read-only role (synthetic data)"

    async def refresh(self) -> Snapshot:
        return await self.snapshot(refresh=True)

    async def context(self, period_code: str | None = None) -> DemoContext:
        snapshot = await self.snapshot()
        period = period_code or self.settings.period_code
        if period not in {r["period_code"] for r in snapshot.records.rows("exam_periods")}:
            raise DemoServiceError(404, "M3_PERIOD_NOT_FOUND", f"Exam period {period} is not in the m3_demo data.")
        inputs = self._inputs.get(period)
        if inputs is None:
            try:
                inputs = build_demo_inputs(snapshot.records, institution_id=self.settings.institution_id,
                                           period_code=period, demo_reference=f"{DEMO_REFERENCE_PREFIX}{period}")
            except DemoDataError as exc:
                raise DemoServiceError(422, "M3_DEMO_DATA_INVALID", "The m3_demo data failed validation.",
                                       exc.problems) from None
            self._inputs[period] = inputs
        return DemoContext(snapshot, inputs, period, self.settings.institution_id)

    # --- Duty allocation ---------------------------------------------------------------------------

    def _solver_settings(self, seconds: float | None) -> SolverSettings:
        limit = min(seconds or self.settings.max_solver_seconds, self.settings.max_solver_seconds)
        return SolverSettings(time_limit_seconds=limit)

    async def allocate(self, mode: AllocationMode, period_code: str | None = None,
                       time_limit_seconds: float | None = None) -> StoredAllocation:
        context = await self.context(period_code)
        try:  # CP-SAT blocks, so it runs off the event loop
            result = await asyncio.to_thread(run_demo_duty_allocation, context.snapshot.records, context.inputs,
                                             mode=mode, settings=self._solver_settings(time_limit_seconds))
        except RuntimeError as exc:
            raise DemoServiceError(503, "M3_SOLVER_UNAVAILABLE", str(exc)) from None
        stored = StoredAllocation(uuid.uuid4().hex, dt.datetime.now(dt.timezone.utc), context, mode, result)
        self.store.add(stored.run_id, stored)
        return stored

    def allocation(self, run_id: str) -> StoredAllocation:
        return self.store.get(run_id, StoredAllocation)

    @staticmethod
    def _eligibility(run: StoredAllocation):
        policies, supplemental = run.context.inputs.policies, run.context.inputs.supplemental
        return replace(policies.eligibility, restrictions=policies.eligibility.restrictions + (supplemental.restrictions or ()))

    @staticmethod
    def _fairness(run: StoredAllocation):
        # Same filtering as the orchestrator: rows of staff excluded at import are ignored.
        inputs = run.context.inputs
        sup, known = inputs.supplemental, {m.staff_id for m in inputs.staff.staff.staff}
        keep = lambda rows: tuple(r for r in (rows or ()) if r.staff_id in known)
        return (FairnessInputs(keep(sup.duty_history), keep(inputs.staff.admin), keep(sup.day_preferences)),
                inputs.policies.fairness)

    def _roster_for(self, run: StoredAllocation, label: str | None) -> tuple[str, tuple[DutyAssignment, ...]]:
        if run.orchestration.allocation_input is None:
            raise DemoServiceError(409, "M3_NO_ROSTER", "This run produced no usable input; there is no roster.")
        label = label or ("FAIR" if run.orchestration.fair is not None else "BASELINE")
        roster = run.roster(label)
        if roster is None:
            raise DemoServiceError(409, "M3_ROSTER_NOT_IN_RUN", f"This run has no {label} roster.")
        return label, roster

    def validate(self, run_id: str, label: str | None, assignments: tuple[DutyAssignment, ...] | None):
        """Independent validation (D1-D10) of a run's roster, or of a submitted roster against the run's data."""
        run = self.allocation(run_id)
        label, roster = self._roster_for(run, label)
        checked = assignments if assignments is not None else roster
        data = run.orchestration.allocation_input
        try:
            violations = validate_allocation(data, self._eligibility(run), run.context.inputs.policies.limits, checked)
        except SchedulingValidationError as exc:
            violations = exc.violations
        return run, label, checked, tuple(violations)

    def _change_args(self, run: StoredAllocation, label: str | None):
        label, roster = self._roster_for(run, label)
        policies = run.context.inputs.policies
        return label, roster, (run.orchestration.allocation_input, self._eligibility(run), policies.limits)

    def preview_swap(self, run_id: str, label: str | None, first: DutyAssignment, second: DutyAssignment):
        run = self.allocation(run_id)
        label, roster, (data, eligibility, limits) = self._change_args(run, label)
        proposal = propose_swap(data, eligibility, limits, roster, first, second, fairness=self._fairness(run))
        return run, label, roster, proposal, self._recheck(run, roster, proposal)

    def preview_replacement(self, run_id: str, label: str | None, duty: DutyAssignment, reason: str, *,
                            unavailable: tuple[dt.datetime, dt.datetime] | None = None,
                            preferred_staff_id: str | None = None):
        run = self.allocation(run_id)
        label, roster, (data, eligibility, limits) = self._change_args(run, label)
        proposal = propose_replacement(data, eligibility, limits, roster, duty, reason, unavailable=unavailable,
                                       preferred_staff_id=preferred_staff_id, fairness=self._fairness(run))
        return run, label, roster, proposal, self._recheck(run, roster, proposal)

    def _recheck(self, run: StoredAllocation, roster, proposal: ChangeProposal) -> tuple[Violation, ...] | None:
        if proposal.proposed_roster is None:
            return None
        return check_proposal(roster, proposal, self._eligibility(run), run.context.inputs.policies.limits,
                              self._fairness(run))

    # --- Seating -----------------------------------------------------------------------------------

    async def seat(self, period_code: str | None = None) -> StoredSeating:
        context = await self.context(period_code)
        result = run_demo_seating(context.snapshot.records, context.inputs)
        stored = StoredSeating(uuid.uuid4().hex, dt.datetime.now(dt.timezone.utc), context, result)
        self.store.add(stored.seating_id, stored)
        return stored

    def seating(self, seating_id: str) -> StoredSeating:
        return self.store.get(seating_id, StoredSeating)
