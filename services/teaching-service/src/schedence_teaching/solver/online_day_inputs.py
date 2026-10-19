"""Opt-in batch-specific online lecture day (P2.4); legacy day/stream calendars are unchanged.

A batch is one institution's (academic_year, semester, stream); a solver input
is always scoped to one institution. Each batch with lectures receives exactly
one solver-selected candidate weekday on which it has online lectures only.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from schedence_teaching.schemas import SessionType, Stream
from schedence_teaching.schemas.models import InputContract, RequiredText

from .attendance_inputs import DeliveryMode, delivery_day, mode_on_day

WEEKDAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY")
NAMED_DAYS = (*WEEKDAYS, "SATURDAY", "SUNDAY")


class BatchKey(StrEnum):
    ACADEMIC_YEAR_SEMESTER_STREAM = "ACADEMIC_YEAR_SEMESTER_STREAM"


class BatchOnlineDayPolicy(InputContract):
    """Confirmed P2.4 rules are recorded explicitly; there are no percentages or quotas."""

    policy_reference: RequiredText
    batch_key: BatchKey
    candidate_days: dict[Stream, set[RequiredText]] = Field(min_length=1)
    # Decision 2: only online lectures for the batch on its selected day.
    selected_day_online_lectures_only: Literal[True]
    # Decision 3: every batch with lectures has at least one on its selected day.
    require_online_lecture_on_selected_day: Literal[True]

    @model_validator(mode="after")
    def weekday_candidates(self) -> Self:
        for days in self.candidate_days.values():
            if not days:
                raise ValueError("each listed stream needs at least one candidate online day")
            if days & set(NAMED_DAYS) - set(WEEKDAYS):
                raise ValueError("candidate online days must be Monday to Friday")
        return self


def check_calendar(config):
    """Static agreement with configured windows and the existing delivery calendar."""
    policy = config.batch_online_days
    if config.delivery_calendar is None:
        raise ValueError("Batch online days require an explicit delivery calendar.")
    days = {day.day: day for day in config.days}
    for stream, candidates in policy.candidate_days.items():
        for day in candidates:
            if day not in days or stream not in days[day].windows:
                raise ValueError("Candidate online days must be configured days with that stream's teaching windows.")
            rule = delivery_day(config, day, stream)
            if SessionType.LECTURE not in rule.allowed_session_types:
                raise ValueError("Candidate online days must permit lectures in the delivery calendar.")
    for rule in config.delivery_calendar.days:
        # Any fixed online row would otherwise allow online lectures off the selected day.
        if rule.mode is DeliveryMode.ONLINE and rule.day not in policy.candidate_days.get(rule.stream, ()):
            raise ValueError("Every online delivery-calendar day must be a candidate online day under batch online-day policy.")


@dataclass(frozen=True)
class CheckedBatchOnlineDays:
    policy: BatchOnlineDayPolicy
    session_batches: dict  # occurrence_id -> (academic_year, semester, stream)
    candidates: dict  # applicable batch -> tuple of configured day indices


def checked_batch_online_days(prepared, config, day_dates=None):
    """Batches come from group metadata and must agree with offerings; names are never parsed."""
    from .results import SolverInputError
    policy = config.batch_online_days
    groups = {row.group_id: row for row in prepared.input_snapshot.student_groups}
    offerings = {row.offering_id: row for row in prepared.input_snapshot.module_offerings}
    for row in groups.values():
        parent = groups.get(row.parent_group_id)
        if parent is not None and (parent.academic_year, parent.semester, parent.stream) != (row.academic_year, row.semester, row.stream):
            raise SolverInputError("A subgroup's academic year, semester or stream differs from its parent; batch identity is ambiguous.")
    session_batches, lectures = {}, set()
    for session in prepared.sessions:
        group, offering = groups[session.group_id], offerings[session.offering_id]
        batch = (group.academic_year, group.semester, group.stream)
        if (offering.academic_year, offering.semester, offering.stream) != batch or session.stream != group.stream:
            raise SolverInputError("Module offering, session requirement and student group disagree on academic batch.")
        session_batches[session.occurrence_id] = batch
        if session.session_type is SessionType.LECTURE:
            lectures.add(batch)
    for index, day in enumerate(config.days):
        if not any(day.day in days for days in policy.candidate_days.values()):
            continue
        on_date = (day_dates or {}).get(day.day)
        if on_date is None and day.day not in WEEKDAYS:
            raise SolverInputError("A candidate online day needs a weekday name or an explicit calendar date.")
        if on_date is not None and (on_date.weekday() > 4 or (day.day in NAMED_DAYS and NAMED_DAYS[on_date.weekday()] != day.day)):
            raise SolverInputError("Candidate online day, weekday name and calendar date disagree or fall on a weekend.")
    candidates = {}
    for batch in sorted(lectures):
        indices = tuple(index for index, day in enumerate(config.days) if day.day in policy.candidate_days.get(batch[2], ()))
        if not indices:
            raise SolverInputError(f"Year {batch[0]} semester {batch[1]} {batch[2].value} has lectures but no candidate online weekday.")
        candidates[batch] = indices
    return CheckedBatchOnlineDays(policy, session_batches, candidates)


def booking_mode(config, context, selection, session, day):
    """The one mode rule: a lecture is online exactly on its batch's selected day."""
    if context is None:
        return mode_on_day(config, day, session.stream)
    batch = context.session_batches[session.occurrence_id]
    if batch in context.candidates and selection.get(batch) == day and session.session_type is SessionType.LECTURE:
        return DeliveryMode.ONLINE
    return mode_on_day(config, day, session.stream)


def inferred_selection(context, schedule):
    """Online days per batch as actually booked, from stored booking modes."""
    selected = {}
    for item in schedule:
        if getattr(item, "delivery_mode", None) is DeliveryMode.ONLINE:
            selected.setdefault(context.session_batches.get(item.session.occurrence_id), set()).add(item.day)
    return selected


def batch_online_issues(config, context, schedule):
    """Independent P2.4 checks; returns (code, message, occurrence IDs)."""
    issues, booked = [], inferred_selection(context, schedule)
    names = {index: day.day for index, day in enumerate(config.days)}
    for batch, days in booked.items():
        if batch not in context.candidates:
            issues.append(("online_without_batch_lectures", "Only batches with lectures may book online sessions.", ()))
        elif len(days) > 1:
            issues.append(("multiple_batch_online_days", "A batch has online sessions on more than one day.", ()))
        elif not days <= {names[index] for index in context.candidates[batch]}:
            issues.append(("non_candidate_online_day", "A batch's online day is not a configured candidate weekday.", ()))
    for batch in context.candidates:
        if len(booked.get(batch, ())) != 1:
            issues.append(("missing_batch_online_lecture", "Every batch with lectures needs online lectures on exactly one selected day.", ()))
    selection = {batch: next(iter(days)) for batch, days in booked.items() if len(days) == 1}
    for item in schedule:
        batch = context.session_batches.get(item.session.occurrence_id)
        if batch in selection and item.day == selection[batch] and (
                item.session.session_type is not SessionType.LECTURE or item.delivery_mode is not DeliveryMode.ONLINE):
            issues.append(("batch_online_day_not_online_lecture",
                           "Only online lectures may run for a batch on its selected online day.", (item.session.occurrence_id,)))
    return issues, selection


def add_batch_online_day(model, config, session, day_variable, choices, lectures_on, identity):
    """Link one booking to its batch's day choice; returns a lecture's online indicator, else None.

    Other session types may not use the selected day (Decision 2). A fixed
    online-only calendar day can host the batch only as its selected day.
    """
    lecture, online_terms = session.session_type is SessionType.LECTURE, []
    for index, chosen in choices.items():
        on_day = model.new_bool_var(f"online_day_candidate:{identity}:{index}")
        model.add(day_variable == index).only_enforce_if(on_day)
        model.add(day_variable != index).only_enforce_if(on_day.Not())
        if not lecture:
            model.add_bool_or([on_day.Not(), chosen.Not()])
            continue
        lectures_on[index].append(on_day)
        online = model.new_bool_var(f"online_lecture:{identity}:{index}")
        model.add_bool_and([on_day, chosen]).only_enforce_if(online)
        model.add_bool_or([on_day.Not(), chosen.Not(), online])
        if mode_on_day(config, config.days[index].day, session.stream) is DeliveryMode.ONLINE:
            model.add_implication(on_day, chosen)
        online_terms.append(online)
    return sum(online_terms) if lecture else None
