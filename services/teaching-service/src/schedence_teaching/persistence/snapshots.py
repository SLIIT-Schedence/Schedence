"""Deterministic JSON and replay through existing independent validators."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
import math
from uuid import UUID

from pydantic import BaseModel

from schedence_teaching.preferences import prepare_lecturer_preferences
from schedence_teaching.preparation import prepare_group_combinations, prepare_timetable_input
from schedence_teaching.solver import SolverResult, SolverStatus
from schedence_teaching.solver.attendance_inputs import mode_on_day
from schedence_teaching.solver.evaluation import validate_evaluation_result
from schedence_teaching.solver.results import ScheduledDelivery
from schedence_teaching.workload import prepare_work_periods

from .contracts import SnapshotDocument


from schedence_teaching.schemas.canonical import canonical_json, content_digest


def snapshot_payload(document: SnapshotDocument) -> dict[str, str]:
    """JSONB envelope retaining exact JSON text for order-sensitive old reports.

    Queryable scope/source/run metadata lives in relational columns. Arrays and
    nested map insertion order in existing model_dump_json report snapshots must
    survive PostgreSQL JSONB's object-key reordering.
    """
    canonical_json(document)  # Reject non-JSON/nonfinite values before encoding.
    snapshot = SnapshotDocument.model_validate(document.model_dump(round_trip=True))
    return {"document_json": snapshot.model_dump_json()}


def restore_snapshot(payload: dict) -> SnapshotDocument:
    if set(payload) != {"document_json"} or not isinstance(payload["document_json"], str):
        raise ValueError("unsupported snapshot storage envelope")
    value = json.loads(payload['document_json'])
    assignment = ((value.get('contexts', {}).get('staff_input') or {}).get('assignment_policy'))
    if assignment is not None and 'appointment_permissions' not in assignment:
        raise LegacyPolicySnapshotError('Legacy diagnostic assignment policy lacks appointment_permissions. '
            'Preserve this artifact; explicit reviewed policy rebinding to a new snapshot is required. No defaults were invented.')
    return SnapshotDocument.model_validate_json(payload["document_json"])


class LegacyPolicySnapshotError(ValueError):
    """An incomplete diagnostic policy needs explicit reviewed reconstruction."""


def replay_snapshot(document: SnapshotDocument):
    """Revalidate detached inputs and reconstruct contexts without invoking CP-SAT."""
    from schedence_teaching.solver.consecutive_inputs import checked_consecutive_inputs
    from schedence_teaching.solver.deliveries import checked_combinations, delivery_alternatives
    from schedence_teaching.solver.fairness_inputs import checked_fairness_inputs
    from schedence_teaching.solver.inputs import checked_inputs
    from schedence_teaching.solver.lunch_inputs import checked_student_lunch_inputs
    from schedence_teaching.solver.preference_inputs import checked_preference_inputs
    from schedence_teaching.solver.staff_inputs import checked_staff_inputs
    from schedence_teaching.solver.venue_inputs import checked_venue_inputs
    from schedence_teaching.solver.work_period_inputs import checked_work_period_inputs

    # Revalidation rejects mutated nested contracts and model_copy bypasses.
    snapshot = SnapshotDocument.model_validate(document.model_dump(round_trip=True))
    prepared, config = checked_inputs(prepare_timetable_input(snapshot.teaching_input), snapshot.solver_config)
    raw, calendar = snapshot.contexts, snapshot.calendar
    contexts = {}
    combinations = None
    if raw.combination_input is not None:
        combinations, combination_config = checked_combinations(
            prepared, prepare_group_combinations(raw.combination_input), raw.combination_config)
        contexts.update(combination_input=combinations, combination_config=combination_config)
    if raw.venue_input is not None:
        contexts["venue_input"] = checked_venue_inputs(prepared, config, raw.venue_input)
    staff = None
    periods = None
    if raw.staff_input is not None:
        staff = checked_staff_inputs(prepared, config, raw.staff_input)
        contexts["staff_input"] = staff
    if raw.work_period_input is not None:
        periods = prepare_work_periods(snapshot.teaching_input, staff.workload_input, raw.work_period_input)
        periods, period_config = checked_work_period_inputs(prepared, config, staff, periods, raw.work_period_config)
        contexts.update(work_periods=periods, work_period_config=period_config)
    if raw.preference_input is not None:
        preferences = prepare_lecturer_preferences(snapshot.teaching_input, raw.preference_input)
        checked_preference_inputs(prepared, config, preferences, calendar, raw.preference_policy,
            raw.preference_config, staff_input=staff, work_periods=periods)
        contexts.update(preference_input=preferences, preference_calendar=calendar,
                        preference_policy=raw.preference_policy, preference_config=raw.preference_config)
    if raw.fairness_policy is not None:
        checked_fairness_inputs(prepared, config, raw.fairness_policy, calendar, raw.fairness_config,
                               staff_input=staff, work_periods=periods)
        contexts.update(fairness_policy=raw.fairness_policy, fairness_calendar=calendar, fairness_config=raw.fairness_config)
    alternatives = delivery_alternatives(prepared, combinations)
    if raw.consecutive_policy is not None:
        checked_consecutive_inputs(prepared, config, raw.consecutive_policy, calendar, raw.consecutive_config,
            alternatives, staff_input=staff, work_periods=periods,
            preference_calendar=contexts.get("preference_calendar"), fairness_calendar=contexts.get("fairness_calendar"))
        contexts.update(consecutive_policy=raw.consecutive_policy, consecutive_calendar=calendar,
                        consecutive_config=raw.consecutive_config)
    if raw.student_lunch_policy is not None:
        checked_student_lunch_inputs(prepared, config, raw.student_lunch_policy, calendar, raw.student_lunch_config,
            alternatives, staff_input=staff, work_periods=periods,
            preference_calendar=contexts.get("preference_calendar"), fairness_calendar=contexts.get("fairness_calendar"),
            consecutive_calendar=contexts.get("consecutive_calendar"))
        contexts.update(student_lunch_policy=raw.student_lunch_policy, student_lunch_calendar=calendar,
                        student_lunch_config=raw.student_lunch_config)
    return snapshot, prepared, config, contexts


@dataclass(frozen=True)
class AttemptDocument:
    """A detached write plan; JSON strings prevent accidental nested mutation."""

    snapshot_digest: str
    solver_status: str
    success: bool
    expected_occurrences: int
    scheduled_occurrences: int
    runtime_seconds: float
    result_json: str
    validation_json: str
    deliveries_json: str


def prepare_attempt(document: SnapshotDocument, result: SolverResult) -> AttemptDocument:
    """Never trust attached validation or a reported FEASIBLE status on its own."""
    snapshot, prepared, config, contexts = replay_snapshot(document)
    if not isinstance(result, SolverResult) or type(result.status) is not SolverStatus:
        raise ValueError("a typed solver result is required")
    if type(result.runtime_seconds) not in (int, float) or not math.isfinite(result.runtime_seconds) or result.runtime_seconds < 0:
        raise ValueError("solver runtime must be finite and nonnegative")
    if type(result.original_occurrence_count) is not int or result.original_occurrence_count != len(prepared.sessions):
        raise ValueError("solver result must reference exactly the snapshot demand")
    success = result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    summary = None
    rows = []
    if success:
        if config.objective_stage_plan is not None:
            # P3.1/P3.2: a combined solve is published only after every enabled component is recomputed.
            from schedence_teaching.solver.objective_stage_plan import validate_combined_result
            summary = validate_combined_result(prepared, config, result, **contexts)
        else:
            summary = validate_evaluation_result(prepared, config, result, **contexts)
        if (not summary.valid_for_implemented_constraints or summary.assigned_occurrences != len(prepared.sessions)):
            raise ValueError("only complete independently validated results can become timetable versions")
        groups = {group.group_id: group for group in snapshot.teaching_input.student_groups}
        for booking in result.schedule:
            combined = isinstance(booking, ScheduledDelivery)
            coverage = booking.covered_sessions if combined else (booking.session,)
            rows.append({
                "delivery_reference": booking.delivery_id if combined else booking.session.occurrence_id,
                "teaching_date": snapshot.calendar.day_dates[booking.day],
                "start_minute": booking.start_minute, "end_minute": booking.end_minute,
                # Stored mode equals the calendar mode for legacy results (validated above).
                "delivery_mode": (booking.delivery_mode if getattr(booking, "delivery_mode", None) is not None
                                  else mode_on_day(config, booking.day, coverage[0].stream)).value,
                "staff_reference": booking.assigned_staff_id, "venue_reference": booking.assigned_venue_id,
                "student_count": booking.combined_student_count if combined else groups[booking.session.group_id].student_count,
                "coverage": [{
                    "occurrence_reference": session.occurrence_id, "requirement_reference": session.requirement_id,
                    "offering_reference": session.offering_id, "group_reference": session.group_id,
                    "session_type": session.session_type, "duration_minutes": session.duration_minutes,
                    "academic_year": groups[session.group_id].academic_year,
                    "semester": groups[session.group_id].semester, "stream": session.stream,
                    "specialization": snapshot.group_specializations[session.group_id],
                } for session in coverage],
            })
        # The foundation persists venue-allocated drafts, never an unallocated
        # physical solve. The existing solver still supports those earlier steps.
        if any(row["delivery_mode"] == "PHYSICAL" and row["venue_reference"] is None for row in rows):
            raise ValueError("physical timetable persistence requires validated venue allocation")
    elif result.schedule or result.validation is not None or result.administrative_schedule:
        raise ValueError("unsuccessful solver attempts cannot claim scheduled assignments or validation")
    policy = snapshot.solver_config.resolved_policy
    policy_evidence = ({'resolved_policy_digest': policy.digest, 'institution_policy_revision': policy.revision}
                       if policy is not None else {})
    return AttemptDocument(
        snapshot_digest=content_digest(snapshot), solver_status=result.status.value, success=success,
        expected_occurrences=len(prepared.sessions), scheduled_occurrences=len(prepared.sessions) if success else 0,
        runtime_seconds=result.runtime_seconds, result_json=canonical_json(result.to_dict()),
        validation_json=canonical_json({"assessment": "PASSED" if success else "NOT_ASSESSED",
            "independent_summary": summary.to_dict() if summary is not None else None, **policy_evidence}),
        deliveries_json=canonical_json(rows),
    )
