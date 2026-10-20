"""Editable PP1 settings; academic rules and generated registrations stay distinct."""

from typing import Annotated

from pydantic import Field

from schedence_teaching.schemas import Stream
from schedence_teaching.schemas.models import AcademicYear, InputContract, RequiredText, Semester
from schedence_teaching.schemas.time import TimeWindow

from .electives import ElectiveAllocationPolicy
from .research_requirements import ResearchRequirementInput


class BlockedAcademicScope(InputContract):
    academic_year: AcademicYear
    semester: Semester
    stream: Stream
    specialization: RequiredText
    reason: RequiredText

    @property
    def key(self):
        return self.academic_year,self.semester,self.stream.value,self.specialization


class ElectiveRunConfiguration(InputContract):
    configuration_reference: RequiredText
    catalog_classification_reference: RequiredText
    weekday_online_day: RequiredText
    weekend_online_day: RequiredText
    weekday_online_window: TimeWindow
    weekend_online_window: TimeWindow
    allocation_policy: ElectiveAllocationPolicy
    research: ResearchRequirementInput
    blocked_scopes: tuple[BlockedAcademicScope, ...] = ()
    scope_solve_seconds: Annotated[float,Field(gt=0,allow_inf_nan=False)]
    global_solve_seconds: Annotated[float,Field(gt=0,allow_inf_nan=False)]
