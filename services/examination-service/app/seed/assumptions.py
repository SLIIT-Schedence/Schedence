"""Configurable assumptions behind the synthetic dataset.

Module data comes from the cleaned real catalogue (``data/module_catalog.csv``).
Everything else here is a **modelling assumption for development and testing**,
not a real institutional (e.g. SLIIT) policy. Change values here, never inside
the generator.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
ALL_VENUE_TYPES = ("auditorium", "exam_hall", "lecture_hall", "computer_lab")


@dataclass(frozen=True)
class SeedAssumptions:
    # Fixed seed: the same assumptions always produce the same dataset.
    random_seed: int = 20261019
    catalog_path: Path = DATA_DIR / "module_catalog.csv"

    # --- Scope (PP1 prototype) ---------------------------------------------------
    specializations: tuple[str, ...] = ("IT", "SE", "DS", "CYBER")

    # --- Students (synthetic) ----------------------------------------------------
    # (academic year, specialization, cohort size). Total = 390.
    cohort_sizes: tuple[tuple[int, str, int], ...] = (
        (1, "IT", 35), (1, "SE", 30), (1, "DS", 20), (1, "CYBER", 20),
        (2, "IT", 33), (2, "SE", 29), (2, "DS", 19), (2, "CYBER", 19),
        (3, "IT", 31), (3, "SE", 28), (3, "DS", 18), (3, "CYBER", 18),
        (4, "IT", 30), (4, "SE", 26), (4, "DS", 17), (4, "CYBER", 17),
    )
    # IDs look like IT30315600: prefix + 2-digit fictional intake + 6 random digits.
    # Intakes 30-33 (year 4 -> 30 ... year 1 -> 33) are deliberately in the
    # future so they cannot match a real student ID.
    student_id_prefix: str = "IT"
    student_intake_base: int = 30

    # --- Repeat registrations ----------------------------------------------------
    repeat_student_fraction: float = 0.15  # of year 2-4 students
    max_repeat_modules_per_student: int = 2

    # --- Exam requirements -----------------------------------------------------------
    # Minutes added when the catalogue says Extra_Reading_Time = Yes (assumption).
    reading_time_minutes: int = 10
    written_venue_types: tuple[str, ...] = ("auditorium", "exam_hall", "lecture_hall")
    computer_based_venue_types: tuple[str, ...] = ("computer_lab",)
    # Hybrid exams: any venue by default. Restrict globally here, or per module below.
    hybrid_venue_types: tuple[str, ...] = ALL_VENUE_TYPES
    hybrid_lab_required_modules: tuple[str, ...] = ()

    # --- Venues --------------------------------------------------------------------
    # Exam seating as a share of normal capacity for auditoriums and lecture halls.
    # Exam halls use full capacity; labs use their number of working machines.
    exam_seating_ratio: float = 0.5

    # --- Examination period ----------------------------------------------------------
    period_code: str = "PP1-2026-S2-FINAL"
    period_name: str = "Synthetic Semester 2 Final Examinations (PP1)"
    academic_year_label: str = "2026/2027"
    semester: int = 2
    period_start: dt.date = dt.date(2026, 11, 16)  # Monday
    period_end: dt.date = dt.date(2026, 11, 28)  # Saturday (weekends off)
    day_start: dt.time = dt.time(8, 30)
    day_end: dt.time = dt.time(17, 30)
    include_saturdays: bool = False
    include_sundays: bool = False
    excluded_dates: tuple[tuple[dt.date, str], ...] = (
        (dt.date(2026, 11, 24), "Simulated non-examination day (placeholder for institutional calendar)"),
    )

    # --- Venue blocks ------------------------------------------------------------------
    teaching_block_count: int = 22
    maintenance_block_count: int = 5
    teaching_block_slots: tuple[tuple[dt.time, dt.time], ...] = (
        (dt.time(8, 30), dt.time(10, 30)),
        (dt.time(10, 30), dt.time(12, 30)),
        (dt.time(13, 30), dt.time(15, 30)),
        (dt.time(15, 30), dt.time(17, 30)),
    )
    maintenance_block_slots: tuple[tuple[dt.time, dt.time], ...] = (
        (dt.time(8, 30), dt.time(12, 30)),
        (dt.time(13, 30), dt.time(17, 30)),
        (dt.time(8, 30), dt.time(17, 30)),
    )
    teaching_venue_types: tuple[str, ...] = ("lecture_hall", "computer_lab")

    def venue_types_for(self, exam_type: str, module_code: str) -> tuple[str, ...]:
        """Venue types an exam of this type may use."""
        if exam_type == "written":
            return self.written_venue_types
        if exam_type == "computer_based":
            return self.computer_based_venue_types
        if module_code in self.hybrid_lab_required_modules:
            return ("computer_lab",)
        return self.hybrid_venue_types


DEFAULT_ASSUMPTIONS = SeedAssumptions()
