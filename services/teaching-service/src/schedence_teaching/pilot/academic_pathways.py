"""Explicit teaching tracks; provisional allocations are not enrollment policy.

Only source-row-identical curricula may be pooled. Original allocation strata
remain traceable through count-based PP1 slices, never invented student records.
"""

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import Field, model_validator

from schedence_teaching.schemas.models import AcademicYear, InputContract, RequiredText, Semester

from .students import CohortCount, generate_groups


class AcademicTrackRule(InputContract):
    academic_year: AcademicYear
    semester: Semester
    track_id: RequiredText
    label: RequiredText
    source_specializations: tuple[RequiredText, ...] = Field(min_length=1)
    kind: Literal['COMMON_FOUNDATION', 'FOUNDATION_CURRICULUM_VARIANT', 'SPECIALIZATION']
    evidence_reference: RequiredText

    @model_validator(mode='after')
    def unique_sources(self):
        if len(set(self.source_specializations)) != len(self.source_specializations):
            raise ValueError('A track cannot repeat a provisional source specialization.')
        return self


class AcademicPathwayConfiguration(InputContract):
    reference: RequiredText
    interpretation: Literal['SOURCE_MODULE_APPLICABILITY_NOT_ENROLLMENT_START']
    rules: tuple[AcademicTrackRule, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def unambiguous_rules(self):
        seen, tracks = set(), set()
        for rule in self.rules:
            key = rule.academic_year, rule.semester, rule.track_id
            if key in tracks:
                raise ValueError('Each period/track needs one rule.')
            tracks.add(key)
            for spec in rule.source_specializations:
                key = rule.academic_year, rule.semester, spec
                if key in seen:
                    raise ValueError('A provisional allocation cannot enter multiple tracks in the same period.')
                seen.add(key)
        return self


@dataclass(frozen=True)
class AcademicGrouping:
    plan: object
    group_source_specializations: dict[str, tuple[str, ...]]
    group_allocation_slices: dict[str, tuple[dict, ...]]
    report: dict


def curriculum_signature(catalog, year, semester, specialization):
    """Module code is deliberately not a unique key, including IT2100."""
    return tuple(sorted((m.source.location.workbook, m.source.location.worksheet, m.source.location.row)
        for m in catalog.select(academic_year=year, semester=semester, specialization=specialization)))


def audit_module_pathways(catalog):
    periods = sorted({(m.source.contract_fields['academic_year'], m.source.contract_fields['semester']) for m in catalog.modules})
    specs = sorted({s for m in catalog.modules for s in m.specializations})
    equivalent = []
    for year, semester in periods:
        signatures = defaultdict(list)
        for spec in specs:
            signatures[curriculum_signature(catalog, year, semester, spec)].append(spec)
        for signature, members in sorted(signatures.items()):
            equivalent.append(dict(academic_year=year, semester=semester, provisional_specializations=members,
                source_rows=[dict(workbook=w, worksheet=s, row=r) for w, s, r in signature],
                mapped=bool(signature)))
    first = {}
    for spec in specs:
        candidates = [m for m in catalog.modules if m.specializations == (spec,)]
        if candidates:
            period = min((m.source.contract_fields['academic_year'], m.source.contract_fields['semester']) for m in candidates)
            first[spec] = dict(academic_year=period[0], semester=period[1])
    return dict(first_exclusive_module_appearance=first, curriculum_equivalence=equivalent,
        official_enrollment_start_inferred=False)


def prepare_academic_grouping(allocations, catalog, configuration):
    """Repartition only explicit periods, conserving every source allocation.

    Slice offsets are anonymous positions inside an approved *estimated* PP1
    allocation, not student identifiers or evidence of real registration.
    Parent slices equal the exact union of their children's slices.
    """
    config = AcademicPathwayConfiguration.model_validate(configuration.model_dump())
    if not allocations.valid or not catalog.valid:
        raise ValueError('Validated allocations and source catalog are required.')
    if allocations.sizing.main_group_maximum > 120 or allocations.sizing.lab_subgroup_maximum > 60:
        raise ValueError('The PP1 academic pathway requires main-group maximum 120 and subgroup maximum 60.')
    source = tuple(CohortCount.model_validate(c.model_dump()) for c in allocations.cohorts)
    periods = {(r.academic_year, r.semester) for r in config.rules}
    rules = {(r.academic_year, r.semester, s): r for r in config.rules for s in r.source_specializations}
    all_specs = {s for m in catalog.modules for s in m.specializations}
    for rule in config.rules:
        if set(rule.source_specializations) - all_specs:
            raise ValueError('Track references an unknown source specialization.')
        signatures = {curriculum_signature(catalog, rule.academic_year, rule.semester, s) for s in rule.source_specializations}
        if len(signatures) != 1 or not next(iter(signatures)):
            raise ValueError('Ambiguous track: pooled populations must have identical nonempty source-row curricula.')
    buckets = defaultdict(list)
    for row in source:
        period = row.academic_year, row.semester
        rule = rules.get((*period, row.specialization))
        if period in periods and rule is None:
            raise ValueError('Every positive allocation in a configured period needs an explicit track rule.')
        key = (*period, row.stream, rule.track_id if rule else row.specialization)
        buckets[key].append(row)
    cohorts, sources_by_cohort, specs_by_cohort = [], {}, {}
    for (year, semester, stream, track), rows in sorted(buckets.items()):
        configured = (year, semester) in periods
        rule = rules[year, semester, rows[0].specialization] if configured else None
        identity = f'PP1:ACADEMIC-TRACK:Y{year}S{semester}:{stream.value}:{track}' if configured else rows[0].cohort_id
        if not configured and len(rows) != 1:
            raise ValueError('Duplicate unconfigured source cohort.')
        rows = sorted(rows, key=lambda r: r.cohort_id)
        cohorts.append(CohortCount(cohort_id=identity, academic_year=year, semester=semester, stream=stream,
            student_count=sum(r.student_count for r in rows), specialization=track, source=rows[0].source,
            source_label=f'PP1 teaching track; {config.reference}; original allocation rows retained in slice provenance' if configured else rows[0].source_label))
        sources_by_cohort[identity] = rows
        specs_by_cohort[identity] = rule.source_specializations if rule else (rows[0].specialization,)
    plan = generate_groups(cohorts, allocations.sizing)
    slices, applicability = {}, {}
    for cohort in plan.cohorts:
        rows = sources_by_cohort[cohort.cohort_id]
        offsets = []
        position = 0
        for row in rows:
            offsets.append((position, position+row.student_count, row))
            position += row.student_count
        def take(start, count):
            result = []
            for first, end, source_row in offsets:
                lo, hi = max(start, first), min(start+count, end)
                if lo < hi:
                    result.append(dict(source_cohort_id=source_row.cohort_id, source_specialization=source_row.specialization,
                        source_first_offset=lo-first, student_count=hi-lo,
                        source=asdict(source_row.source)))
            if sum(r['student_count'] for r in result) != count:
                raise ValueError('Allocation slices do not reconcile to the group size.')
            return tuple(result)
        start = 0
        mains = [g for g in plan.groups if plan.cohort_by_group[g.group_id] == cohort.cohort_id and g.parent_group_id is None]
        for main in mains:
            slices[main.group_id] = take(start, main.student_count)
            child_start = start
            for child in (g for g in plan.groups if g.parent_group_id == main.group_id):
                slices[child.group_id] = take(child_start, child.student_count)
                child_start += child.student_count
            if child_start != start+main.student_count:
                raise ValueError('Subgroups must exactly partition their generated PP1 parent.')
            start = child_start
        if start != cohort.student_count:
            raise ValueError('Main groups do not partition the exact source population.')
    for group in plan.groups:
        applicability[group.group_id] = specs_by_cohort[plan.cohort_by_group[group.group_id]]
    before, after = defaultdict(int), defaultdict(int)
    for row in source:
        before[row.academic_year, row.semester, row.stream.value] += row.student_count
    for row in plan.cohorts:
        after[row.academic_year, row.semester, row.stream.value] += row.student_count
    if before != after:
        raise ValueError('Original year/semester/stream totals changed.')
    report = dict(configuration=config.model_dump(mode='json'), original_allocation_source_sha256=allocations.source_sha256,
        original_allocation_rows=[r.model_dump(mode='json') for r in source],
        reconciled_cohorts=[dict(academic_year=y, semester=s, stream=t, original_students=n, grouped_students=after[y,s,t])
            for (y,s,t),n in sorted(before.items())],
        group_allocation_slices=slices, slice_interpretation='COUNT_BASED_PP1_PARTITION_NOT_INDIVIDUAL_REGISTRATION',
        actual_individual_membership_claimed=False, original_allocations_preserved=True,
        official_enrollment_start_inferred=False, shared_lecture_combining=False,
        track_rules_require_identical_source_row_curricula=True)
    return AcademicGrouping(plan, applicability, slices, report)
