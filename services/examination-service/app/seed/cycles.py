"""SYNTHETIC examination cycle mappings and repeat-only registrations (testing only).

The real cycle membership must come from the institution. This module builds a
clearly synthetic one on top of the synthetic dataset, so full-cycle generation
can be tested:

* every dataset module of the period's semester that has an examination of the
  period's kind is mapped with scope ``all`` (normal and repeat candidates);
* a few real catalogue modules of the *other* semester are added and mapped
  ``repeat_only``, with synthetic repeat registrations of later-year students of
  the same specialization: modules re-examined only for repeat candidates.

Deterministic (fixed seed) and additive: seeding the result (``python -m
app.seed --with-synthetic-cycle``) inserts the new rows, keeps every existing row
and changes nothing on a second run.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

from app.seed.assumptions import DEFAULT_ASSUMPTIONS, SeedAssumptions
from app.seed.catalog import CatalogModule, load_catalog
from app.seed.generator import _module
from app.seed.records import Dataset, ExamPeriodModule, ModuleSpecialization, Registration
from app.sources.exam_rules import exam_duration_minutes


@dataclass(frozen=True)
class SyntheticCycleAssumptions:
    random_seed: int = 20261020
    repeat_only_modules: int = 3  # other-semester catalogue modules re-examined for repeat candidates
    min_repeat_candidates: int = 2
    max_repeat_candidates: int = 6


DEFAULT_CYCLE_ASSUMPTIONS = SyntheticCycleAssumptions()


def build_synthetic_cycle(
    data: Dataset,
    cycle: SyntheticCycleAssumptions = DEFAULT_CYCLE_ASSUMPTIONS,
    assumptions: SeedAssumptions = DEFAULT_ASSUMPTIONS,
    catalog: tuple[CatalogModule, ...] | None = None,
) -> Dataset:
    """``data`` plus a SYNTHETIC cycle mapping for its (single) examination period."""
    [period] = data.exam_periods
    rng = random.Random(cycle.random_seed)
    catalog = catalog if catalog is not None else load_catalog(assumptions.catalog_path)
    existing = {m.module_code for m in data.modules}
    scope = set(assumptions.specializations)
    registered = {(r.student_id, r.module_code) for r in data.registrations}

    members = [ExamPeriodModule(period.period_code, m.module_code, "all")
               for m in data.modules
               if m.semester == period.semester and exam_duration_minutes(m, period.exam_kind) is not None]

    # Other-semester modules of years 1-3 (so later-year students can repeat them).
    candidates = sorted((m for m in catalog
                         if m.semester != period.semester and m.module_code not in existing
                         and m.academic_year <= 3 and set(m.specializations) & scope),
                        key=lambda m: m.module_code)
    added_modules, links, registrations = [], [], []
    for m in rng.sample(candidates, min(cycle.repeat_only_modules, len(candidates))):
        module = _module(m, assumptions)
        if exam_duration_minutes(module, period.exam_kind) is None:
            continue
        students = sorted((s for s in data.students
                           if s.academic_year > m.academic_year and s.specialization in m.specializations
                           and (s.student_id, m.module_code) not in registered), key=lambda s: s.student_id)
        if not students:
            continue
        count = min(rng.randint(cycle.min_repeat_candidates, cycle.max_repeat_candidates), len(students))
        added_modules.append(module)
        links.extend(ModuleSpecialization(m.module_code, s) for s in sorted(m.specializations))
        registrations.extend(Registration(s.student_id, m.module_code, "repeat") for s in rng.sample(students, count))
        members.append(ExamPeriodModule(period.period_code, m.module_code, "repeat_only"))

    return replace(
        data,
        modules=tuple(sorted(data.modules + tuple(added_modules), key=lambda m: m.module_code)),
        module_specializations=data.module_specializations + tuple(links),
        registrations=tuple(sorted(data.registrations + tuple(registrations),
                                   key=lambda r: (r.student_id, r.module_code))),
        exam_period_modules=tuple(sorted(data.exam_period_modules + tuple(members),
                                         key=lambda c: (c.period_code, c.module_code))),
    )
