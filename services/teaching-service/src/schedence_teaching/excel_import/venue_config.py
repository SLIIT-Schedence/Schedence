"""Explicit mappings for venue inputs; verification is supplied outside Excel."""

from pathlib import Path
from typing import Literal

from pydantic import model_validator

from schedence_teaching.schemas import Venue
from schedence_teaching.schemas.models import InputContract, RequiredText

from .wide_config import DatasetSheet


class VenueImportConfig(InputContract):
    layout: Literal["venue-v1"]
    venues: DatasetSheet
    columns: dict[RequiredText, RequiredText]
    source_metadata_columns: tuple[RequiredText, ...] = ()
    unknown_location_values: tuple[RequiredText, ...] = ()
    verification_file: Path | None = None

    @model_validator(mode="after")
    def check_columns(self):
        if set(self.columns) != set(Venue.model_fields):
            raise ValueError("columns must map every Venue field exactly once")
        headers = [*self.columns.values(), *self.source_metadata_columns]
        if len(set(headers)) != len(headers):
            raise ValueError("mapped and metadata headers must be distinct")
        return self
