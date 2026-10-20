"""In-memory end-to-end staff duty allocation for one Member 2 DraftTimetable (Member 3).

    DraftTimetable (read only)
      -> staff + admin workload (Step 9A client, or preloaded AcademicStaffData)
      -> calculate_requirements(staffing policy)
      -> DutyAllocationInput (+ explicitly supplied unavailability / availability)
      -> eligibility policy (+ explicitly supplied restrictions)
      -> allocate_duties (baseline) / allocate_fair_duties / compare_duty_fairness
      -> validate_allocation again on every roster
      -> OrchestrationResult: rosters, fairness metrics, data availability, diagnostics

Nothing is stored, approved, published or applied: every result is an offline draft.
Data the Academic Data Service does not provide (unavailability, availability, duty
history, day preferences, lecturer-module restrictions) is used only when supplied. None
means "not available"; an empty tuple means "supplied and empty". Fairness results say
which factors were actually covered.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, replace
from enum import Enum

from app.duty_allocation.academic_adapter import AcademicStaffData, load_academic_staff_data
from app.duty_allocation.academic_client import AcademicDataClient
from app.duty_allocation.allocation_policy import DutyLimitPolicy
from app.duty_allocation.eligibility_policy import EligibilityPolicy, StaffRestriction
from app.duty_allocation.fairness import AVOIDED_DAY, allocate_fair_duties, compare_duty_fairness, fairness_input_errors
from app.duty_allocation.fairness_metrics import FairnessMetrics, is_avoided_day, measure_fairness, staff_offsets
from app.duty_allocation.fairness_policy import (
    DayPreference, DutyFairnessPolicy, DutyHistory, FairnessInputs, MissingAdminHours,
)
from app.duty_allocation.requirements import RequirementCalculation, calculate_requirements
from app.duty_allocation.solver import allocate_duties
from app.duty_allocation.staffing_policy import StaffingPolicy
from app.duty_allocation.types import (
    DUTY_INPUT, DutyAllocationInput, DutyAllocationResult, DutyAllocationStatus, StaffAvailability,
    StaffUnavailability,
)
from app.duty_allocation.validation import validate_allocation
from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling.policy import SolverSettings
from app.scheduling.types import DraftTimetable

DRAFT_LABEL = "OFFLINE IN-MEMORY DRAFT: not saved, approved, published or applied."
_OK = (DutyAllocationStatus.OPTIMAL, DutyAllocationStatus.FEASIBLE)


class AllocationMode(str, Enum):
    BASELINE = "BASELINE"  # Step 4: hard constraints only
    FAIR = "FAIR"  # Step 5: fairness-aware objective
    COMPARE = "COMPARE"  # both, measured with the same metrics


class OrchestrationStatus(str, Enum):
    COMPLETED = "COMPLETED"  # every requested roster found and independently validated
    INFEASIBLE = "INFEASIBLE"  # proven: no roster satisfies the hard constraints
    UNKNOWN = "UNKNOWN"  # solver stopped without a roster or a proof
    INVALID_INPUT = "INVALID_INPUT"  # inputs, policies or data cannot be used as given
    VALIDATION_FAILED = "VALIDATION_FAILED"  # a roster failed the independent re-check


class DataStatus(str, Enum):
    PROVIDED = "PROVIDED"
    PROVIDED_EMPTY = "PROVIDED_EMPTY"  # explicitly supplied as an empty dataset
    NOT_AVAILABLE = "NOT_AVAILABLE"  # no source; the factor is not considered


class DataOrigin(str, Enum):
    ACADEMIC_DATA_CLIENT = "ACADEMIC_DATA_CLIENT"  # loaded just now through the Step 9A client
    PRELOADED = "PRELOADED"  # passed in by the caller (offline, synthetic or cached); not verified as live
    SUPPLEMENTAL = "SUPPLEMENTAL"  # SupplementalStaffData supplied by the caller
    NONE = "NONE"  # nothing was loaded or supplied


@dataclass(frozen=True)
class DataAvailability:
    name: str
    status: DataStatus
    source: str
    records: int = 0
    origin: DataOrigin = DataOrigin.NONE


@dataclass(frozen=True)
class AllocationPolicies:
    staffing: StaffingPolicy
    eligibility: EligibilityPolicy
    limits: DutyLimitPolicy
    fairness: DutyFairnessPolicy | None = None  # required for FAIR and COMPARE
    settings: SolverSettings = SolverSettings()


@dataclass(frozen=True)
class SupplementalStaffData:
    """Data the Academic Data Service does not have. None = not available."""
    source: str
    unavailability: tuple[StaffUnavailability, ...] | None = None
    availability: tuple[StaffAvailability, ...] | None = None
    duty_history: tuple[DutyHistory, ...] | None = None
    day_preferences: tuple[DayPreference, ...] | None = None
    restrictions: tuple[StaffRestriction, ...] | None = None  # e.g. lecturer-module conflicts


NO_SUPPLEMENTAL_DATA = SupplementalStaffData("none supplied")


@dataclass(frozen=True)
class OrchestrationResult:
    status: OrchestrationStatus
    mode: AllocationMode
    label: str = DRAFT_LABEL
    allocation_input: DutyAllocationInput | None = None
    requirements: RequirementCalculation | None = None
    baseline: DutyAllocationResult | None = None
    fair: DutyAllocationResult | None = None
    baseline_metrics: FairnessMetrics | None = None
    fair_metrics: FairnessMetrics | None = None
    revalidation: tuple[tuple[str, tuple[Violation, ...]], ...] = ()  # (BASELINE|FAIR, violations)
    data_availability: tuple[DataAvailability, ...] = ()
    fairness_scope: tuple[str, ...] = ()  # factors actually covered by the fairness result
    fairness_complete: bool = False  # True only if history, preferences and admin hours were all available
    violations: tuple[Violation, ...] = ()
    diagnostics: tuple[str, ...] = ()
    saved: bool = False  # always False: nothing is persisted
    approved: bool = False  # always False
    published: bool = False  # always False

    @property
    def roster(self):
        chosen = self.fair if self.mode is not AllocationMode.BASELINE else self.baseline
        return chosen.assignments if chosen is not None else ()


def _invalid(mode, *messages, violations=(), **kwargs) -> OrchestrationResult:
    return OrchestrationResult(OrchestrationStatus.INVALID_INPUT, mode,
                               violations=tuple(violations) + tuple(Violation(DUTY_INPUT, m) for m in messages), **kwargs)


def _availability(name: str, rows, source: str) -> DataAvailability:
    if rows is None:
        return DataAvailability(name, DataStatus.NOT_AVAILABLE, "no source")
    return DataAvailability(name, DataStatus.PROVIDED if rows else DataStatus.PROVIDED_EMPTY,
                            f"supplemental: {source}", len(rows), DataOrigin.SUPPLEMENTAL)


_SOURCE_TEXT = {DataOrigin.ACADEMIC_DATA_CLIENT: "Academic Data Service (loaded via client)",
                DataOrigin.PRELOADED: "preloaded by caller (offline/synthetic; not verified as live)"}


def _split(rows, known: set[str], excluded: set[str], name: str):
    """Keep rows for imported staff; report rows for excluded staff; reject unknown staff."""
    if rows is None:
        return (), [], []
    kept, notes, errors = [], [], []
    for row in rows:
        if row.staff_id in known:
            kept.append(row)
        elif row.staff_id in excluded:
            notes.append(f"{name} for staff {row.staff_id} ignored: that staff member was excluded at import.")
        else:
            errors.append(f"{name} refers to staff {row.staff_id}, who is not in the Academic Data staff list.")
    return tuple(kept), notes, errors


def plan_duty_allocation(draft: DraftTimetable, academic: AcademicStaffData, policies: AllocationPolicies, *,
                         mode: AllocationMode = AllocationMode.COMPARE,
                         supplemental: SupplementalStaffData = NO_SUPPLEMENTAL_DATA,
                         allow_staff_import_errors: bool = False,
                         staff_origin: DataOrigin = DataOrigin.PRELOADED,
                         admin_loaded: bool | None = None) -> OrchestrationResult:
    """Run the whole in-memory workflow on staff data. Inputs are never modified.

    staff_origin says where ``academic`` came from (PRELOADED unless the Step 9A client just
    loaded it). admin_loaded=False means workload was deliberately not fetched; None infers
    it from whether any workload rows or missing-workload records are present."""
    if type(mode) is not AllocationMode:
        return _invalid(AllocationMode.BASELINE, "mode must be an AllocationMode.")
    if staff_origin not in _SOURCE_TEXT:
        return _invalid(mode, "staff_origin must be ACADEMIC_DATA_CLIENT or PRELOADED.")
    if type(draft) is not DraftTimetable or type(academic) is not AcademicStaffData \
            or type(policies) is not AllocationPolicies or type(supplemental) is not SupplementalStaffData:
        return _invalid(mode, "Needs a DraftTimetable, AcademicStaffData, AllocationPolicies and SupplementalStaffData.")
    bad = [name for name, value, kind in (("staffing", policies.staffing, StaffingPolicy),
                                          ("eligibility", policies.eligibility, EligibilityPolicy),
                                          ("limits", policies.limits, DutyLimitPolicy),
                                          ("settings", policies.settings, SolverSettings))
           if type(value) is not kind]
    if policies.fairness is not None and type(policies.fairness) is not DutyFairnessPolicy:
        bad.append("fairness")
    if bad:
        return _invalid(mode, f"Invalid policy objects: {', '.join(bad)}.")
    if mode is not AllocationMode.BASELINE and policies.fairness is None:
        return _invalid(mode, f"{mode.value} mode needs a DutyFairnessPolicy.")

    staff_import = academic.staff
    source_text = _SOURCE_TEXT[staff_origin]
    diagnostics = [DRAFT_LABEL, f"Staff data source: {source_text}."]
    if staff_import.violations and not allow_staff_import_errors:
        return _invalid(mode, "Staff import has unreadable records; fix them or allow them explicitly.",
                        violations=staff_import.violations, diagnostics=tuple(diagnostics))
    if staff_import.violations:
        diagnostics.append(f"{len(staff_import.violations)} staff records could not be read and were left out "
                           "(allowed explicitly).")
    for excluded in staff_import.excluded:
        diagnostics.append(f"Staff {excluded.staff_number} excluded: {excluded.reason}.")
    if not staff_import.staff:
        return _invalid(mode, "No staff are eligible for any duty.", diagnostics=tuple(diagnostics))

    requirements = calculate_requirements(draft, policies.staffing)
    if not requirements.is_complete:
        return _invalid(mode, "The staffing policy does not cover every room; no requirement was guessed.",
                        violations=requirements.violations, requirements=requirements, diagnostics=tuple(diagnostics))

    known = {m.staff_id for m in staff_import.staff}
    excluded_ids = {e.staff_id for e in staff_import.excluded}
    pieces, errors = {}, []
    for name, rows in (("unavailability", supplemental.unavailability), ("availability", supplemental.availability),
                       ("duty history", supplemental.duty_history), ("day preferences", supplemental.day_preferences),
                       ("restrictions", supplemental.restrictions)):
        kept, notes, problems = _split(rows, known, excluded_ids, name)
        pieces[name] = kept
        diagnostics += notes
        errors += problems
    if errors:
        return _invalid(mode, *errors, requirements=requirements, diagnostics=tuple(diagnostics))

    try:
        data = DutyAllocationInput(draft, staff_import.staff, requirements.requirements,
                                   pieces["unavailability"], pieces["availability"])
        eligibility = replace(policies.eligibility,
                              restrictions=policies.eligibility.restrictions + pieces["restrictions"])
        inputs = FairnessInputs(pieces["duty history"], academic.admin, pieces["day preferences"])
    except SchedulingValidationError as exc:
        return _invalid(mode, "Duty allocation input is invalid.", violations=exc.violations,
                        requirements=requirements, diagnostics=tuple(diagnostics))

    if admin_loaded is None:
        admin_loaded = bool(academic.admin or academic.missing_workload)
    if academic.admin:
        admin_item = DataAvailability("admin workload", DataStatus.PROVIDED, source_text, len(academic.admin), staff_origin)
    elif admin_loaded:
        admin_item = DataAvailability("admin workload", DataStatus.NOT_AVAILABLE,
                                      f"{source_text}: no workload records", 0, staff_origin)
    else:
        admin_item = DataAvailability("admin workload", DataStatus.NOT_AVAILABLE, "not loaded")
    availability = (
        DataAvailability("staff", DataStatus.PROVIDED, source_text, len(staff_import.staff), staff_origin),
        admin_item,
        _availability("unavailability", supplemental.unavailability, supplemental.source),
        _availability("availability", supplemental.availability, supplemental.source),
        _availability("duty history", supplemental.duty_history, supplemental.source),
        _availability("day preferences", supplemental.day_preferences, supplemental.source),
        _availability("restrictions", supplemental.restrictions, supplemental.source),
    )
    for item in availability:
        if item.status is DataStatus.NOT_AVAILABLE:
            diagnostics.append(f"{item.name}: NOT AVAILABLE; not considered.")
    common = dict(allocation_input=data, requirements=requirements, data_availability=availability)

    scope, complete, metrics_available = (), False, True
    if policies.fairness is not None:
        _, missing_admin = staff_offsets(data, inputs, policies.fairness)
        rejecting = bool(missing_admin) and policies.fairness.missing_admin_hours is MissingAdminHours.REJECT
        # BASELINE runs only the hard constraints, so REJECT (a fairness-data rule) cannot block it;
        # every other fairness-input check still applies. FAIR and COMPARE use the policy as given.
        check = (replace(policies.fairness, missing_admin_hours=MissingAdminHours.ZERO)
                 if mode is AllocationMode.BASELINE else policies.fairness)
        problems = fairness_input_errors(data, inputs, check)
        if problems:
            return _invalid(mode, "Fairness inputs are invalid for this policy.", violations=problems,
                            diagnostics=tuple(diagnostics), **common)
        if rejecting:  # only reachable in BASELINE
            metrics_available = False
            diagnostics.append(f"Baseline fairness metrics UNAVAILABLE: admin hours missing or NOT_COMPUTED for "
                               f"{len(missing_admin)} staff and missing_admin_hours=REJECT; none were estimated.")
        elif missing_admin:
            diagnostics.append(f"Admin hours missing or NOT_COMPUTED for {len(missing_admin)} staff; counted as 0 "
                               f"because missing_admin_hours={policies.fairness.missing_admin_hours.value}.")
        for missing in academic.missing_workload:
            diagnostics.append(f"No workload record for staff {missing.staff_id}: {missing.reason}")
        covered = {"duty balance": True, "role weights": True,
                   "exam duration": policies.fairness.load_unit.value == "MINUTES",
                   "back-to-back duties": policies.fairness.back_to_back_minutes > 0,
                   "duty history": supplemental.duty_history is not None,
                   "day preferences": supplemental.day_preferences is not None,
                   "admin workload": policies.fairness.admin_load_per_weekly_hour > 0 and bool(academic.admin)
                   and not missing_admin}
        scope = tuple(name for name, ok in covered.items() if ok)
        complete = covered["duty history"] and covered["day preferences"] and covered["admin workload"]
        if not complete:
            diagnostics.append("Fairness is PARTIAL: it covers only " + ", ".join(scope) + ".")

    baseline = fair = None
    if mode is AllocationMode.BASELINE:
        baseline = allocate_duties(data, eligibility, policies.limits, policies.settings)
    elif mode is AllocationMode.FAIR:
        fair = allocate_fair_duties(data, eligibility, policies.limits, inputs, policies.fairness, policies.settings)
    else:
        comparison = compare_duty_fairness(data, eligibility, policies.limits, inputs, policies.fairness,
                                           policies.settings)
        baseline, fair = comparison.baseline, comparison.fair

    def recheck(result: DutyAllocationResult | None):
        if result is None or result.status not in _OK:
            return None
        found = validate_allocation(data, eligibility, policies.limits, result.assignments)
        if policies.fairness is not None and policies.fairness.hard_avoid_days and result is fair:
            sessions = {s.session_id: s for s in draft.sessions}
            found += tuple(Violation(AVOIDED_DAY, f"Staff {a.staff_id} works {a.session_id} on an avoided day.")
                           for a in result.assignments
                           if is_avoided_day(a.staff_id, sessions[a.session_id].start.date(), inputs))
        return found

    revalidation = tuple((label, found) for label, found in (("BASELINE", recheck(baseline)), ("FAIR", recheck(fair)))
                         if found is not None)
    metrics = lambda r: (measure_fairness(data, inputs, policies.fairness, r.assignments)
                         if metrics_available and policies.fairness is not None and r is not None and r.status in _OK
                         else None)
    requested = [r for r in (baseline, fair) if r is not None]
    statuses = {r.status for r in requested}
    violations = tuple(v for r in requested for v in r.violations)
    if any(found for _, found in revalidation):
        status = OrchestrationStatus.VALIDATION_FAILED
        violations += tuple(v for _, found in revalidation for v in found)
    elif statuses <= set(_OK):
        status = OrchestrationStatus.COMPLETED
    elif DutyAllocationStatus.MODEL_INVALID in statuses:
        status = OrchestrationStatus.INVALID_INPUT
    elif DutyAllocationStatus.INFEASIBLE in statuses:
        status = OrchestrationStatus.INFEASIBLE
    else:
        status = OrchestrationStatus.UNKNOWN
    diagnostics += [d for r in requested for d in r.diagnostics]
    return OrchestrationResult(status, mode, baseline=baseline, fair=fair, baseline_metrics=metrics(baseline),
                               fair_metrics=metrics(fair), revalidation=revalidation, fairness_scope=scope,
                               fairness_complete=complete, violations=violations,
                               diagnostics=tuple(dict.fromkeys(diagnostics)), **common)


async def plan_duty_allocation_from_academic(client: AcademicDataClient, token: str, *, institution_id: uuid.UUID,
                                             draft: DraftTimetable, policies: AllocationPolicies,
                                             mode: AllocationMode = AllocationMode.COMPARE,
                                             supplemental: SupplementalStaffData = NO_SUPPLEMENTAL_DATA,
                                             as_of: dt.date | None = None,
                                             allow_staff_import_errors: bool = False) -> OrchestrationResult:
    """Load staff and admin workload with the caller's token (Step 9A), then plan.
    Academic Data errors (auth, permission, outage, malformed or foreign data) propagate."""
    if type(policies) is not AllocationPolicies:
        return _invalid(mode if type(mode) is AllocationMode else AllocationMode.BASELINE, "Needs AllocationPolicies.")
    # Workload only matters for the fairness objective; a BASELINE-only run never fetches it.
    needs_admin = (mode is not AllocationMode.BASELINE and policies.fairness is not None
                   and policies.fairness.admin_load_per_weekly_hour > 0)
    academic = await load_academic_staff_data(client, token, institution_id=institution_id,
                                              eligibility=policies.eligibility, include_workload=needs_admin, as_of=as_of)
    return plan_duty_allocation(draft, academic, policies, mode=mode, supplemental=supplemental,
                                allow_staff_import_errors=allow_staff_import_errors,
                                staff_origin=DataOrigin.ACADEMIC_DATA_CLIENT, admin_loaded=needs_admin)
