"""Shared half-open minute windows, usable without importing the solver."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from .models import InputContract

MINUTES_PER_DAY = 24 * 60


class TimeWindow(InputContract):
    """Half-open interval of minutes from midnight, within a single day."""

    start_minute: Annotated[int, Field(strict=True, ge=0, lt=MINUTES_PER_DAY)]
    end_minute: Annotated[int, Field(strict=True, gt=0, le=MINUTES_PER_DAY)]

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.end_minute <= self.start_minute:
            raise ValueError("window end must be after its start")
        return self
