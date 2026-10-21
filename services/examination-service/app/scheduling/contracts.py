"""Runtime shape checks shared by scheduler boundaries; no solver dependency."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass

from app.scheduling.runtime_budget import check_budget


@dataclass(frozen=True)
class Violation:
    constraint: str
    message: str


class SchedulingValidationError(ValueError):
    """An object cannot be constructed safely; callers can inspect violations."""

    def __init__(self, violations: tuple[Violation, ...]):
        self.violations = tuple(violations)
        super().__init__("; ".join(v.message for v in self.violations))


def frozen_sequence(value: object, label: str, item_type: type | None = None) -> tuple:
    """Materialize ordered iterables once. Reject text, mappings and unordered sets."""
    if isinstance(value, (str, bytes, bytearray, Mapping, Set)) or not isinstance(value, Iterable):
        raise SchedulingValidationError((Violation("H11", f"{label} must be an ordered iterable."),))
    try:
        result = tuple(value)
    except Exception as exc:
        raise SchedulingValidationError((Violation("H11", f"{label} could not be read as an ordered iterable."),)) from exc
    if item_type is not None and any(type(item) is not item_type for item in result):
        raise SchedulingValidationError((Violation("H11", f"{label} must contain {item_type.__name__} values."),))
    return result


def text_errors(value: object, label: str, *, optional: bool = False) -> tuple[Violation, ...]:
    if optional and value is None:
        return ()
    if type(value) is not str or not value.strip():
        return (Violation("H11", f"{label} must be a non-empty string."),)
    return ()


def record_errors(rows, record_type: type, label: str, *, text=(), optional_text=(), fields=()) -> tuple[Violation, ...]:
    """Check types before any identifier hashing, sorting, or time comparisons."""
    errors = []
    for index, row in enumerate(rows):
        check_budget()
        name = f"{label}[{index}]"
        if type(row) is not record_type:
            errors.append(Violation("H11", f"{name} must be a {record_type.__name__}."))
            continue
        for field in text:
            errors.extend(text_errors(getattr(row, field), f"{name}.{field}"))
        for field in optional_text:
            errors.extend(text_errors(getattr(row, field), f"{name}.{field}", optional=True))
        for field, expected in fields:
            if type(getattr(row, field)) is not expected:
                errors.append(Violation("H11", f"{name}.{field} must be a {expected.__name__}."))
    return tuple(errors)


def data_shape_errors(period, modules, students, registrations, venues, blocks, capacities=None) -> tuple[Violation, ...]:
    """Validate source record shapes before loading capacities or forming lookup keys."""
    import datetime as dt
    from app.sources.types import ExamPeriod, ExamPeriodExclusion, Module, Registration, Student, Venue, VenueBlock
    from app.seating.snapshot import CapacitySnapshot, SnapshotVenue

    errors = []
    for rows, kind, label, texts, optional, fields in (
        (modules, Module, "modules", ("module_code", "exam_type"), (),
         (("module_name", str), ("duration_minutes", int), ("reading_time_minutes", int), ("candidate_count", int),
          ("academic_year", int), ("semester", int), ("credits", int), ("difficulty", int), ("has_extra_reading_time", bool),
          ("has_final_exam", bool), ("has_mid_exam", bool), ("delivery_mode", str))),
        (students, Student, "students", ("student_id",), (), (("academic_year", int), ("specialization", str))),
        (registrations, Registration, "registrations", ("student_id", "module_code", "registration_type"), (), ()),
        (venues, Venue, "venues", ("venue_id", "venue_type"), (),
         (("venue_name", str), ("building", str), ("normal_capacity", int), ("exam_capacity", int), ("floor", int))),
        (blocks, VenueBlock, "blocks", ("venue_id", "source", "reason"), ("source_ref",),
         (("block_date", dt.date), ("start_time", dt.time), ("end_time", dt.time))),
        ((period,), ExamPeriod, "period", ("period_code",), (),
         (("period_name", str), ("academic_year_label", str), ("exam_kind", str),
          ("start_date", dt.date), ("end_date", dt.date),
          ("day_start_time", dt.time), ("day_end_time", dt.time),
          ("semester", int), ("include_saturdays", bool), ("include_sundays", bool))),
    ):
        errors.extend(record_errors(rows, kind, label, text=texts, optional_text=optional, fields=fields))
    if type(period) is ExamPeriod:
        errors.extend(record_errors(period.exclusions, ExamPeriodExclusion, "exclusions", text=("period_code",),
                                    fields=(("excluded_date", dt.date), ("reason", str))))
    if capacities is not None:
        if type(capacities) is not CapacitySnapshot:
            errors.append(Violation("H11", "capacities must be a CapacitySnapshot."))
        else:
            errors.extend(record_errors((capacities,), CapacitySnapshot, "capacity snapshot", text=("fingerprint",),
                                        fields=(("ruleset_version", int), ("default_percentage", int), ("taken_at", dt.datetime))))
            errors.extend(record_errors(capacities.venues, SnapshotVenue, "capacities",
                                        text=("venue_id", "venue_type", "rule_scope"),
                                        fields=(("normal_capacity", int), ("percentage", int), ("exam_capacity", int))))
    return tuple(errors)
