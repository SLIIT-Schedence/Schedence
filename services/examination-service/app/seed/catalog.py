"""Module catalogue (from the cleaned CSV) and the synthetic venue list.

Modules are real catalogue records produced by ``app.seed.catalog_import``.
Venue names and capacities are synthetic.
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


# (venue_id, name, type, normal capacity, working machines for labs / None)
# Exam capacity (assumptions, see generator._exam_capacity):
#   exam_hall    = normal capacity (seating is already spaced for exams)
#   computer_lab = working machines
#   other halls  = normal capacity x exam_seating_ratio
VENUES: tuple[tuple[str, str, str, int, int | None], ...] = (
    ("V-AUD", "Main Auditorium", "auditorium", 300, None),
    ("V-EXH", "Examination Hall A", "exam_hall", 100, None),
    ("V-EXH2", "Examination Hall B", "exam_hall", 80, None),
    ("V-LH1", "Lecture Hall 1", "lecture_hall", 100, None),
    ("V-LH2", "Lecture Hall 2", "lecture_hall", 80, None),
    ("V-LAB1", "Computer Lab 1", "computer_lab", 60, 45),
    ("V-LAB2", "Computer Lab 2", "computer_lab", 40, 30),
    ("V-LAB3", "Computer Lab 3", "computer_lab", 60, 50),
    ("V-LAB4", "Computer Lab 4", "computer_lab", 50, 40),
)
