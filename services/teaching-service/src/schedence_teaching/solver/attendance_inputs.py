"""Explicit delivery calendars and population evidence, independent of combining."""

from enum import StrEnum
from itertools import combinations

from pydantic import Field, model_validator, model_serializer

from schedence_teaching.schemas import GroupMembershipEvidence, SessionType, Stream
from schedence_teaching.schemas.models import InputContract, RequiredText


class DeliveryMode(StrEnum):
    PHYSICAL = "PHYSICAL"
    ONLINE = "ONLINE"


class DeliveryDay(InputContract):
    day: RequiredText
    stream: Stream
    mode: DeliveryMode
    allowed_session_types: set[SessionType] = Field(min_length=1)
    mode_policy_reference: RequiredText | None = None

    @model_serializer(mode='wrap')
    def historical_payload(self, handler):
        result = handler(self)
        if self.mode_policy_reference is None:
            result.pop('mode_policy_reference', None)
        return result

    @model_validator(mode="after")
    def online_lectures_only(self):
        if (self.mode_policy_reference is None and self.mode == DeliveryMode.ONLINE
                and self.allowed_session_types != {SessionType.LECTURE}):
            raise ValueError("The current online arrangement supports lectures only.")
        return self


class DeliveryCalendar(InputContract):
    reference: RequiredText
    days: tuple[DeliveryDay, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def distinct_days(self):
        if len({(row.day, row.stream) for row in self.days}) != len(self.days):
            raise ValueError("Each stream/day must have one explicit delivery mode.")
        return self


def delivery_day(config, day, stream):
    if config.delivery_calendar is None:
        return None
    return next(row for row in config.delivery_calendar.days if (row.day, row.stream) == (day, stream))


def permitted_day(config, session, day):
    rule = delivery_day(config, day, session.stream)
    return rule is None or session.session_type in rule.allowed_session_types


def mode_on_day(config, day, stream):
    rule = delivery_day(config, day, stream)
    return rule.mode if rule else DeliveryMode.PHYSICAL


def checked_attendance_memberships(prepared, config):
    """Complete test rosters are evidence only within their declared input scope."""
    from .results import SolverInputError
    groups = {row.group_id: row for row in prepared.input_snapshot.student_groups}
    found = set()
    for row in config.attendance_memberships:
        if row.group_id not in groups or row.group_id in found:
            raise SolverInputError("Attendance roster references an unknown or duplicate group.")
        found.add(row.group_id)
        if not row.complete or len(row.student_ids) != groups[row.group_id].student_count:
            raise SolverInputError("Explicit scheduling rosters must be complete and match student counts.")
    rosters = {row.group_id: set(row.student_ids) for row in config.attendance_memberships}
    if config.attendance_required_group_ids - groups.keys() or config.attendance_required_group_ids - rosters.keys():
        raise SolverInputError("Every registered-population group requires its complete attendance roster; none may be omitted.")
    for group_id, students in rosters.items():
        current = groups[group_id].parent_group_id
        while current is not None:
            if current in rosters and not students <= rosters[current]:
                raise SolverInputError("Child membership must be a subset of every supplied ancestor roster.")
            current = groups[current].parent_group_id
    return rosters


def membership_overlap_pairs(config):
    rosters = [(row.group_id, set(row.student_ids)) for row in config.attendance_memberships]
    return tuple((first, second) for (first, left), (second, right) in combinations(rosters, 2) if left & right)
