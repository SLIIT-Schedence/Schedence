"""New two-cycle endpoints; single-cycle request and save contracts stay unchanged."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.deps import PERIOD_CODE
from app.api.schemas import GenerateTimetableIn, GeneratedTimetableOut, GenerationCalendarIn, SavedTimetableOut, SettingsProblemOut


class BothCycleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period_code: str = Field(pattern=PERIOD_CODE)
    calendar: GenerationCalendarIn | None = None


class BothSemestersIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cycles: list[BothCycleIn] = Field(min_length=2, max_length=2)
    mode: Literal["feasibility", "optimized"] = "feasibility"
    time_limit_seconds: float | None = Field(None, gt=0, le=300)
    scheduling_preferences: dict | None = None
    include_analysis: bool = False

    @model_validator(mode="after")
    def checked(self):
        if len({c.period_code for c in self.cycles}) != 2:
            raise ValueError("Select two distinct examination cycles")
        # Use the existing approved preference validation, never a second set of rules.
        self.scheduling_preferences = GenerateTimetableIn(
            period_code=self.cycles[0].period_code, scheduling_preferences=self.scheduling_preferences).scheduling_preferences
        return self


class PairValidationOut(BaseModel):
    passed: bool | None
    violations: list[SettingsProblemOut]


class BothGeneratedOut(BaseModel):
    batch_id: str
    generated: bool
    sequential_order: list[str]
    skipped_period_codes: list[str]
    results: list[GeneratedTimetableOut]
    pair_validation: PairValidationOut
    shared_candidates: int | None
    notices: list[str]


class BothSavedOut(BaseModel):
    batch_id: str
    timetables: list[SavedTimetableOut]
    notices: list[str]
