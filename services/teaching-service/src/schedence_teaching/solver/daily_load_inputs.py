"""Opt-in daily workload distribution (P2.2): minimax student-group and staff daily teaching minutes.

Pure minimax objectives: no thresholds, targets or hard daily caps are introduced.
Students are the lowest-level groups, inheriting every ancestor group's deliveries.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from schedence_teaching.schemas.models import InputContract, RequiredText

from .compactness_inputs import CompactnessPlacement as StagePlacement, CRITERION as THIN

STUDENT = "MAX_STUDENT_DAILY_MINUTES"
STAFF = "MAX_STAFF_DAILY_MINUTES"


class DailyLoadCriterion(StrEnum):
    MAX_STUDENT_DAILY_MINUTES = STUDENT
    MAX_STAFF_DAILY_MINUTES = STAFF


class DailyLoadDistributionConfig(InputContract):
    policy_reference: RequiredText
    # Explicit research priority among the selected criteria, highest first.
    criteria: Annotated[tuple[DailyLoadCriterion, ...], Field(min_length=1, max_length=2)]
    placement: StagePlacement
    # Required only when P2.1 compactness shares this placement; never inferred.
    relative_to_compactness: Literal["DAILY_LOAD_THEN_COMPACTNESS", "COMPACTNESS_THEN_DAILY_LOAD"] | None = None

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len(set(self.criteria)) != len(self.criteria):
            raise ValueError("daily-load criteria must be distinct")
        return self


def quality_order(base_order, *, compactness=None, daily=None, fairness_enabled):
    """Insert P2.1/P2.2 stages at explicit anchors without reordering existing criteria."""
    blocks = []
    if daily is not None:
        blocks.append((daily.placement, tuple(row.value for row in daily.criteria)))
    if compactness is not None:
        blocks.append((compactness.placement, (THIN,)))
    if daily is not None and compactness is not None and daily.placement is compactness.placement:
        if daily.relative_to_compactness is None:
            raise ValueError("Daily-load and compactness stages share a placement; choose relative_to_compactness explicitly.")
        if daily.relative_to_compactness == "COMPACTNESS_THEN_DAILY_LOAD":
            blocks.reverse()
    order = list(base_order)
    for placement in (StagePlacement.BEFORE_AGGREGATE_TARGET_DEVIATION, StagePlacement.AFTER_WORKLOAD_STAGES):
        criteria = [key for where, keys in blocks if where is placement for key in keys]
        if not criteria:
            continue
        if placement is StagePlacement.BEFORE_AGGREGATE_TARGET_DEVIATION:
            if not fairness_enabled:
                raise ValueError("BEFORE_AGGREGATE_TARGET_DEVIATION placement requires teaching-target fairness optimization.")
            position = order.index("AGGREGATE_TARGET_DEVIATION")
        else:
            position = order.index("PREFERENCE_PENALTY") if "PREFERENCE_PENALTY" in order else len(order)
        order[position:position] = criteria
    return tuple(order)


def add_student_daily_maximum(model, config, prepared, deliveries, day_flag):
    """deliveries: (identity, group IDs, minutes, presence or None, day variable); one term per delivery per leaf.

    day_flag(identity, index, day_variable) returns the shared reified "delivery on this day" flag.
    """
    hierarchy = prepared.hierarchy
    leaves = {identity for identity in hierarchy.parents if not hierarchy.children.get(identity)}
    loads = {}
    weekly = {}
    for identity, group_ids, minutes, presence, day_variable, streams in deliveries:
        day_flags = []
        attending = set()
        for group in group_ids:  # A set: one delivery counts once per leaf, even if several of its groups apply.
            attending.update(leaf for leaf in (group, *hierarchy.descendants(group)) if leaf in leaves)
        for index, day in enumerate(config.days):
            if not streams & set(day.windows):
                continue
            flag = day_flag(identity, index, day_variable)
            day_flags.append(flag)
            if presence is not None:
                present = model.new_bool_var(f"delivery_present_on_day:{identity}:{day.day}")
                model.add_bool_and([flag, presence]).only_enforce_if(present)
                model.add_bool_or([flag.Not(), presence.Not(), present])
                flag = present
            for leaf in attending:
                loads.setdefault((leaf, index), []).append(minutes * flag)
        # Redundant but valid: the start domain places every delivery on exactly one stream day.
        model.add(sum(day_flags) == 1)
        for leaf in attending:
            weekly.setdefault(leaf, []).append(minutes if presence is None else minutes * presence)
    return _maximum(model, loads, "student_daily_minutes", weekly)


def add_staff_daily_maximum(model, day_usage, assignments):
    """Reuse P2.1 staff-day minutes; the weekly link strengthens the minimax bound without changing feasibility."""
    weekly = {staff: [minutes * chosen for _, chosen, _, minutes in rows] for staff, rows in assignments.items()}
    return _maximum(model, {key: [minutes] for key, (minutes, _) in day_usage.items()}, "staff_daily_minutes", weekly)


def _maximum(model, loads, name, weekly):
    """Each entity's daily loads sum to its weekly minutes (a valid redundant link for the LP bound)."""
    if not loads:
        return 0
    terms, by_entity = [], {}
    for (entity, index), parts in sorted(loads.items()):
        load = model.new_int_var(0, 1440, f"{name}:{entity}:{index}")
        model.add(load == sum(parts))
        terms.append(load)
        by_entity.setdefault(entity, []).append(load)
    for entity, days in by_entity.items():
        if entity in weekly:
            model.add(sum(days) == sum(weekly[entity]))
    maximum = model.new_int_var(0, 1440, f"maximum_{name}")
    model.add_max_equality(maximum, terms)
    return maximum


@dataclass(frozen=True)
class DailyLoadReport:
    policy_reference: str
    placement: str
    priority_order: tuple[str, ...]
    stages: tuple[tuple[str, str, int | None], ...]
    modeled_maxima: tuple[tuple[str, int], ...]
    maximum_student_daily_minutes: int
    mean_student_daily_minutes: float
    student_daily_minutes_distribution: tuple[tuple[int, int], ...]
    maximum_staff_daily_minutes: int
    staff_daily_minutes_distribution: tuple[tuple[int, int], ...]

    def to_dict(self):
        return {"policy_reference": self.policy_reference, "placement": self.placement,
                "priority_order": list(self.priority_order),
                "stages": [dict(criterion=c, status=s, objective_value=v) for c, s, v in self.stages],
                "modeled_maxima": dict(self.modeled_maxima),
                "maximum_student_daily_minutes": self.maximum_student_daily_minutes,
                "mean_student_daily_minutes": self.mean_student_daily_minutes,
                "student_daily_minutes_distribution": dict(self.student_daily_minutes_distribution),
                "maximum_staff_daily_minutes": self.maximum_staff_daily_minutes,
                "staff_daily_minutes_distribution": dict(self.staff_daily_minutes_distribution),
                "hard_daily_cap_added": False, "university_ready": False}


def daily_load_report(prepared, config, schedule, order, stages, modeled):
    """Measured independently from the validated schedule with the P2.0 report (active days only)."""
    from collections import Counter
    from schedence_teaching.reporting.timetable_quality import timetable_quality
    quality = timetable_quality(prepared, schedule, config=config)
    students = [row.teaching_minutes for row in quality.student_days]
    staff = [row.teaching_minutes for row in quality.lecturer_days]
    selected = {row.value for row in config.daily_load_distribution.criteria}
    return DailyLoadReport(config.daily_load_distribution.policy_reference, config.daily_load_distribution.placement.value,
        tuple(order), tuple((row.criterion, row.status, row.objective_value) for row in stages if row.criterion in selected),
        tuple(sorted(modeled.items())), max(students, default=0), round(sum(students) / len(students), 2) if students else 0.0,
        tuple(sorted(Counter(students).items())), max(staff, default=0), tuple(sorted(Counter(staff).items())))
