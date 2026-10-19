"""Fairness-aware duty allocation and its comparison with the Step 4 baseline.

Both rosters satisfy the same Step 4 hard constraints and pass the same independent
validator; the fairness-aware one also minimises the configured soft objective.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.duty_allocation.allocation_policy import DutyLimitPolicy
from app.duty_allocation.eligibility_policy import EligibilityPolicy
from app.duty_allocation.fairness_metrics import FairnessMetrics, is_avoided_day, measure_fairness, staff_offsets
from app.duty_allocation.fairness_policy import DutyFairnessPolicy, FairnessInputs, MissingAdminHours
from app.duty_allocation.solver import allocate_duties, solve_duties
from app.duty_allocation.types import DUTY_INPUT, DutyAllocationInput, DutyAllocationResult, DutyAllocationStatus
from app.scheduling.contracts import Violation
from app.scheduling.policy import SolverSettings

AVOIDED_DAY = "D11"  # only when hard_avoid_days is configured


def fairness_input_errors(data: DutyAllocationInput, inputs: FairnessInputs, policy: DutyFairnessPolicy) -> tuple[Violation, ...]:
    if type(inputs) is not FairnessInputs or type(policy) is not DutyFairnessPolicy:
        return (Violation(DUTY_INPUT, "fairness needs FairnessInputs and a DutyFairnessPolicy."),)
    if type(data) is not DutyAllocationInput:
        return ()  # reported by the shared pipeline
    errors = [Violation(DUTY_INPUT, f"fairness inputs reference unknown staff {staff_id}.")
              for staff_id in inputs.unknown_staff(m.staff_id for m in data.staff)]
    _, missing = staff_offsets(data, inputs, policy)
    if missing and policy.missing_admin_hours is MissingAdminHours.REJECT:
        errors += [Violation(DUTY_INPUT, f"admin hours missing for staff {staff_id}.") for staff_id in missing]
    return tuple(errors)


def allocate_fair_duties(data: DutyAllocationInput, eligibility: EligibilityPolicy, limits: DutyLimitPolicy,
                         inputs: FairnessInputs, policy: DutyFairnessPolicy,
                         settings: SolverSettings = SolverSettings()) -> DutyAllocationResult:
    errors = fairness_input_errors(data, inputs, policy)
    if errors:
        return DutyAllocationResult(DutyAllocationStatus.MODEL_INVALID,
                                    data.timetable.input_fingerprint if type(data) is DutyAllocationInput else "unknown",
                                    violations=errors, diagnostics=("Invalid fairness input or policy.",))
    from app.duty_allocation.fairness_model import add_fairness_objective

    sessions = {s.session_id: s for s in data.timetable.sessions}

    def avoided_day_violations(assignments):
        if not policy.hard_avoid_days:
            return ()
        return tuple(Violation(AVOIDED_DAY, f"Staff {a.staff_id} works {a.session_id} on an avoided day.")
                     for a in assignments if is_avoided_day(a.staff_id, sessions[a.session_id].start.date(), inputs))

    result = solve_duties(data, eligibility, limits, settings,
                          extend_model=lambda m: add_fairness_objective(m, data, inputs, policy),
                          extra_checks=avoided_day_violations)
    _, missing = staff_offsets(data, inputs, policy)
    if missing:
        note = f"Admin hours missing for {len(missing)} staff; counted as 0 (missing_admin_hours=ZERO)."
        return DutyAllocationResult(result.status, result.input_fingerprint, result.assignments, result.violations,
                                    result.diagnostics + (note,), result.wall_time_seconds, result.solver_version)
    return result


@dataclass(frozen=True)
class DutyFairnessComparison:
    baseline: DutyAllocationResult
    fair: DutyAllocationResult
    baseline_metrics: FairnessMetrics | None
    fair_metrics: FairnessMetrics | None


def compare_duty_fairness(data: DutyAllocationInput, eligibility: EligibilityPolicy, limits: DutyLimitPolicy,
                          inputs: FairnessInputs, policy: DutyFairnessPolicy,
                          settings: SolverSettings = SolverSettings()) -> DutyFairnessComparison:
    """Baseline = Step 4 allocator (hard constraints only); both measured with the same metrics."""
    baseline = allocate_duties(data, eligibility, limits, settings)
    fair = allocate_fair_duties(data, eligibility, limits, inputs, policy, settings)
    ok = (DutyAllocationStatus.OPTIMAL, DutyAllocationStatus.FEASIBLE)
    can_measure = not fairness_input_errors(data, inputs, policy)
    measure = lambda r: measure_fairness(data, inputs, policy, r.assignments) if can_measure and r.status in ok else None
    return DutyFairnessComparison(baseline, fair, measure(baseline), measure(fair))
