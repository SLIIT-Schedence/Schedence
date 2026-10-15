"""Module and venue catalogues (from the cleaned CSV files in ``data/``).

Modules are real catalogue records produced by ``app.seed.catalog_import``.
Venues are the campus venue list produced by ``app.seed.venue_import``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

EXAM_TYPES = ("written", "computer_based", "hybrid")


@dataclass(frozen=True)
class CatalogModule:
    module_code: str
    module_name: str
    academic_year: int
    semester: int
    specializations: tuple[str, ...]
    credits: int
    exam_type: str
    duration_minutes: int
    has_extra_reading_time: bool
    difficulty: int


@dataclass(frozen=True)
class CatalogVenue:
    venue_id: str
    venue_name: str
    venue_type: str
    building: str
    floor: int
    full_capacity: int


def load_catalog(path: Path) -> tuple[CatalogModule, ...]:
    """Read ``module_catalog.csv`` (one row per module code)."""
    with path.open(newline="", encoding="utf-8") as handle:
        return tuple(
            CatalogModule(
                module_code=row["module_code"],
                module_name=row["module_name"],
                academic_year=int(row["academic_year"]),
                semester=int(row["semester"]),
                specializations=tuple(row["specializations"].split(";")),
                credits=int(row["credits"]),
                exam_type=row["exam_type"],
                duration_minutes=int(row["duration_minutes"]),
                has_extra_reading_time=row["has_extra_reading_time"] == "yes",
                difficulty=int(row["difficulty"]),
            )
            for row in csv.DictReader(handle)
        )


def load_venues(path: Path) -> tuple[CatalogVenue, ...]:
    """Read ``venue_catalog.csv`` (one row per venue code, codes kept exactly)."""
    with path.open(newline="", encoding="utf-8") as handle:
        return tuple(
            CatalogVenue(
                venue_id=row["venue_id"],
                venue_name=row["venue_name"],
                venue_type=row["venue_type"],
                building=row["building"],
                floor=int(row["floor"]),
                full_capacity=int(row["full_capacity"]),
            )
            for row in csv.DictReader(handle)
        )
