"""Opt-in teaching-day compactness (P2.1): minimize thin staff-days as one extra staged objective.

A thin staff-day has assigned teaching minutes greater than zero and strictly
below an explicitly configured preferred minimum. Days without teaching are
never penalized. There is no default minimum and no hard daily cap.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import Field

from schedence_teaching.schemas.models import InputContract, RequiredText

CRITERION = "THIN_TEACHING_DAYS"


class CompactnessPlacement(StrEnum):
    # After every workload/fairness stage, before soft lecturer preferences.
    AFTER_WORKLOAD_STAGES = "AFTER_WORKLOAD_STAGES"
    # Research trade-off: after the proven peak/total overage and maximum
    # deviation stages, but before the aggregate deviation stage, which then
    # optimizes only among compact schedules.
    BEFORE_AGGREGATE_TARGET_DEVIATION = "BEFORE_AGGREGATE_TARGET_DEVIATION"


class TeachingDayCompactnessConfig(InputContract):
    policy_reference: RequiredText
    preferred_minimum_daily_teaching_minutes: Annotated[int, Field(strict=True, gt=0, le=1440)]
    # Provenance of the minimum, for example a synthetic test label; never a campus default.
    minimum_reference: RequiredText
    placement: CompactnessPlacement


def compactness_order(base_order, config, *, fairness_enabled):
    """Insert the one compactness stage without reordering any existing criterion."""
    from .daily_load_inputs import quality_order
    return quality_order(base_order, compactness=config, fairness_enabled=fairness_enabled)


def add_thin_day_flags(model, config, day_usage):
    """day_usage: (staff, day index) -> (teaching-minute expression, active-day flag) from existing staff caps."""
    minimum, flags = config.teaching_day_compactness.preferred_minimum_daily_teaching_minutes, []
    for (staff_id, index), (minutes, active) in sorted(day_usage.items()):
        thin = model.new_bool_var(f"thin_teaching_day:{staff_id}:{config.days[index].day}")
        # Active and not thin requires the preferred minimum; inactive days are never thin.
        model.add(minutes + minimum * thin >= minimum * active)
        model.add(thin <= active)
        # Exact at every feasible point, not only at the optimum: thin <=> active and minutes < minimum. A run that
        # stops before this stage must still report the true count (the independent validator recomputes it).
        model.add(minutes <= minimum - 1).only_enforce_if(thin)
        flags.append(thin)
    return sum(flags)


@dataclass(frozen=True)
class TeachingDayCompactnessReport:
    policy_reference: str
    minimum_reference: str
    preferred_minimum_daily_teaching_minutes: int
    placement: str
    priority_order: tuple[str, ...]
    stage_status: str | None
    modeled_thin_staff_days: int | None
    thin_staff_days: int
    thin_days_by_staff: tuple[tuple[str, int], ...]
    active_staff_days: int
    maximum_daily_teaching_minutes: int
    daily_teaching_minutes_distribution: tuple[tuple[int, int], ...]

    def to_dict(self):
        from dataclasses import asdict
        result = asdict(self)
        result.update(thin_days_by_staff=dict(self.thin_days_by_staff),
                      daily_teaching_minutes_distribution=dict(self.daily_teaching_minutes_distribution),
                      hard_daily_cap_added=False, university_ready=False)
        return result


def compactness_report(prepared, config, schedule, order, stages, modeled):
    """Recomputed from the validated schedule, counting each booking once per staff member."""
    from collections import Counter
    from schedence_teaching.reporting.timetable_quality import timetable_quality
    settings = config.teaching_day_compactness
    days = timetable_quality(prepared, schedule, config=config).lecturer_days
    thin = Counter(row.entity_id for row in days if 0 < row.teaching_minutes < settings.preferred_minimum_daily_teaching_minutes)
    stage = next((row for row in stages if row.criterion == CRITERION), None)
    return TeachingDayCompactnessReport(settings.policy_reference, settings.minimum_reference,
        settings.preferred_minimum_daily_teaching_minutes, settings.placement.value, tuple(order),
        stage.status if stage else None, modeled, sum(thin.values()), tuple(sorted(thin.items())), len(days),
        max((row.teaching_minutes for row in days), default=0),
        tuple(sorted(Counter(row.teaching_minutes for row in days).items())))
