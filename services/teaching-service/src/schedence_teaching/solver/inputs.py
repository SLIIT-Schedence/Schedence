"""Defensive validation of preparation output and time discretization."""

from __future__ import annotations

from schedence_teaching.preparation import PreparedTimetableInput, prepare_timetable_input

from .config import SolverConfig
from .results import SolverInputError


def checked_inputs(
    prepared: PreparedTimetableInput, config: SolverConfig,
) -> tuple[PreparedTimetableInput, SolverConfig]:
    if not isinstance(prepared, PreparedTimetableInput):
        raise SolverInputError("Solver requires prepare_timetable_input() output, not incomplete source staging.")
    if not isinstance(config, SolverConfig):
        raise SolverInputError("Solver requires an explicit SolverConfig.")
    snapshot = prepare_timetable_input(prepared.input_snapshot)
    if snapshot.sessions != prepared.sessions or dict(snapshot.hierarchy.parents) != dict(prepared.hierarchy.parents):
        raise SolverInputError("Prepared sessions/hierarchy no longer match the input contracts; prepare them again.")
    config = SolverConfig.model_validate(config.model_dump(round_trip=True))
    from schedence_teaching.policy.resolution import verify_config
    try:
        verify_config(config)
    except ValueError as error:
        raise SolverInputError(str(error)) from None
    from .attendance_inputs import checked_attendance_memberships
    checked_attendance_memberships(snapshot, config)
    for session in snapshot.sessions:
        if session.duration_minutes % config.granularity_minutes:
            raise SolverInputError(
                f"Occurrence {session.occurrence_id}: duration is not divisible by scheduling granularity."
            )
    return snapshot, config
