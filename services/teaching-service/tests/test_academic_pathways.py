"""Synthetic curricula, allocation conservation and unified attendance views."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from schedence_teaching.excel_import.staging import SourceLocation
from schedence_teaching.pilot.academic import extract_academic_catalog
from schedence_teaching.pilot.academic_pathways import (
    AcademicPathwayConfiguration, audit_module_pathways, prepare_academic_grouping,
)
from schedence_teaching.pilot.allocations import SpecializationAllocations
from schedence_teaching.pilot.group_timetables import main_group_timetables, parallel_booking_lanes, subgroup_timetable
from schedence_teaching.pilot.students import CohortCount, GroupSizingPolicy
from schedence_teaching.schemas import StudentGroup

from test_academic_demand import source


def inputs():
    academic = source(lab=2)
    # Two source rows deliberately have the same code, but are distinct modules.
    for module in academic.source_modules:
        module.contract_fields['specialization'] = 'SYN-A, SYN-B'
    catalog = extract_academic_catalog(academic)
    allocations = SpecializationAllocations(sizing=GroupSizingPolicy(), cohorts=[
        CohortCount(cohort_id=f'SYN-{stream}-{spec}', academic_year=1, semester=1, stream=stream,
            student_count=count, specialization=spec, source=SourceLocation('synthetic.xlsx', 'ALLOC', i+2))
        for i, (stream, spec, count) in enumerate([('WEEKDAY', 'SYN-A', 130), ('WEEKDAY', 'SYN-B', 120),
            ('WEEKEND', 'SYN-A', 30), ('WEEKEND', 'SYN-B', 25)])])
    configuration = AcademicPathwayConfiguration(reference='synthetic:explicit-teaching-track',
        interpretation='SOURCE_MODULE_APPLICABILITY_NOT_ENROLLMENT_START', rules=[dict(academic_year=1,
            semester=1, track_id='FOUNDATION', label='Common foundation', kind='COMMON_FOUNDATION',
            source_specializations=['SYN-A', 'SYN-B'], evidence_reference='synthetic:source-row-curriculum')])
    return allocations, catalog, configuration


def test_source_identical_populations_pool_without_claiming_enrollment_starts():
    allocation, catalog, config = inputs()
    original = deepcopy(allocation.cohorts)
    result = prepare_academic_grouping(allocation, catalog, config)
    assert allocation.cohorts == original
    mains = [g for g in result.plan.groups if g.parent_group_id is None]
    assert sum(g.student_count for g in mains) == 305
    assert sorted(g.student_count for g in mains) == [55, 83, 83, 84]
    assert max(g.student_count for g in result.plan.groups if g.parent_group_id) <= 60
    assert not result.report['actual_individual_membership_claimed']
    assert not result.report['official_enrollment_start_inferred']
    assert set(result.plan.specializations.values()) == {'FOUNDATION'}
    assert result == prepare_academic_grouping(allocation, catalog, config)


def test_source_allocation_slices_preserve_each_stratum_exactly_once():
    allocation, catalog, config = inputs()
    result = prepare_academic_grouping(allocation, catalog, config)
    for row in allocation.cohorts:
        spans = []
        for g in result.plan.groups:
            if g.parent_group_id:
                continue
            for segment in result.group_allocation_slices[g.group_id]:
                if segment['source_cohort_id'] == row.cohort_id:
                    spans.append((segment['source_first_offset'], segment['student_count']))
        cursor = 0
        for start, count in sorted(spans):
            assert start == cursor
            cursor += count
        assert cursor == row.student_count
    for main in (g for g in result.plan.groups if g.parent_group_id is None):
        assert sum(g.student_count for g in result.plan.groups if g.parent_group_id == main.group_id) == main.student_count


@pytest.mark.parametrize('field,value', [('main_group_maximum',121),('lab_subgroup_maximum',61)])
def test_pathway_does_not_relax_confirmed_group_caps(field,value):
    allocation,catalog,config=inputs()
    allocation.sizing=GroupSizingPolicy(**{field:value})
    with pytest.raises(ValueError,match='maximum 120 and subgroup maximum 60'):
        prepare_academic_grouping(allocation,catalog,config)


def test_different_source_rows_cannot_be_pooled_even_with_duplicate_module_codes():
    allocation, catalog, config = inputs()
    catalog.modules[1] = replace(catalog.modules[1], specializations=('SYN-B',))
    with pytest.raises(ValueError, match='identical nonempty source-row curricula'):
        prepare_academic_grouping(allocation, catalog, config)


@pytest.mark.parametrize('change', ['missing-rule', 'duplicate-source', 'duplicate-track', 'unknown-source', 'empty-curriculum'])
def test_ambiguous_or_missing_pathway_rules_fail(change):
    allocation, catalog, config = inputs()
    raw = config.model_dump()
    if change == 'missing-rule':
        raw['rules'][0]['source_specializations'] = ('SYN-A',)
    elif change == 'duplicate-source':
        raw['rules'][0]['source_specializations'] = ('SYN-A', 'SYN-A')
    elif change == 'duplicate-track':
        raw['rules'] = (*raw['rules'], raw['rules'][0])
    elif change == 'unknown-source':
        raw['rules'][0]['source_specializations'] = ('SYN-A', 'UNKNOWN')
    else:
        for index, module in enumerate(catalog.modules):
            catalog.modules[index] = replace(module, specializations=('SYN-A',))
    with pytest.raises(ValueError):
        prepare_academic_grouping(allocation, catalog, AcademicPathwayConfiguration.model_validate(raw))


def test_appearance_audit_does_not_establish_an_enrollment_start():
    _, catalog, _ = inputs()
    report = audit_module_pathways(catalog)
    assert not report['official_enrollment_start_inferred']
    assert report['first_exclusive_module_appearance'] == {}
    assert len(report['curriculum_equivalence']) == 1


def test_pp1_integration_keeps_source_offerings_and_contact_hours():
    from test_pp1_allocations import small_batch
    from schedence_teaching.pilot.pp1 import prepare_pp1
    from schedence_teaching.excel_import import VenueImportResult
    from schedence_teaching.excel_import.staging import SourceRecord
    old = small_batch(lab=2)
    academic = source(lab=2)
    for m in academic.source_modules:
        m.contract_fields['specialization'] = 'SYN-A, SYN-B'
    allocation, _, config = inputs()
    rooms = VenueImportResult(venues=list(old.solver_inputs['venue_input'].venues))
    rooms.source_venues = [SourceRecord(SourceLocation('synthetic-rooms.xlsx','Venues',i+2),{})
        for i in range(len(rooms.venues))]
    corrected = prepare_pp1(allocation,academic,rooms,old.configuration,academic_pathways=config)
    requirements = corrected.prepared.input_snapshot.session_requirements
    # Distinct source rows sharing IT2100 must still have distinct offerings.
    assert len(corrected.prepared.input_snapshot.module_offerings) == 4
    assert all(r.duration_minutes == 120 and r.sessions_per_week == 1 for r in requirements)
    assert all(m.specialization == 'SYN-A, SYN-B' for m in corrected.prepared.input_snapshot.module_offerings)
    assert not corrected.metadata['shared_lecture_combining']
    assert set(corrected.group_specializations.values()) == {'FOUNDATION'}
    assert sum(g.student_count for g in corrected.plan.groups if not g.parent_group_id) == 305


def test_direct_demand_builder_rejects_union_or_intersection_curriculum_pooling():
    from test_academic_demand import group_inputs, bindings
    from schedence_teaching.pilot.academic import build_specialization_teaching_input
    academic=source()
    with pytest.raises(ValueError,match='identical nonempty source-row curricula'):
        build_specialization_teaching_input(academic,extract_academic_catalog(academic),offerings=bindings(academic),
            **group_inputs(),group_source_specializations={'SYN-G-A':('SYN-A','SYN-B'),'SYN-G-B':('SYN-B',)})


def groups():
    base = dict(academic_year=1, semester=1, stream='WEEKDAY')
    return [StudentGroup(group_id='P', student_count=120, **base),
        StudentGroup(group_id='A', parent_group_id='P', student_count=60, **base),
        StudentGroup(group_id='B', parent_group_id='P', student_count=60, **base)]


def booking(identity, attendees, start=510, end=630, day='MONDAY'):
    return SimpleNamespace(delivery_id=identity, covered_sessions=(), group_ids=tuple(attendees),
        start_minute=start, end_minute=end, day=day)


def test_one_main_view_contains_parent_and_parallel_sibling_teaching_once():
    bookings = [booking('lecture', ['P'], 510, 630), booking('tutorial-A', ['A'], 630, 690),
        booking('tutorial-B', ['B'], 630, 690), booking('shared', ['A', 'B'], 810, 930)]
    view, = main_group_timetables(groups(), bookings)
    assert len(view.bookings) == 4
    assert view.labels == {'P': 'G1', 'A': 'G1.1', 'B': 'G1.2'}
    assert {b.delivery_id for b in subgroup_timetable(groups(), bookings, 'A')} == {'lecture', 'tutorial-A', 'shared'}
    lanes = {b.delivery_id: (lane, count) for b, lane, count in parallel_booking_lanes(view.bookings)}
    assert lanes['lecture'] == (0, 1)
    assert lanes['tutorial-A'] == (0, 2) and lanes['tutorial-B'] == (1, 2)
    assert lanes['shared'] == (0, 1)


def test_lane_clusters_handle_partial_overlap_and_back_to_back_boundaries():
    items = [booking('A', ['A'], 510, 630), booking('B', ['B'], 570, 690),
        booking('C', ['A'], 630, 750), booking('D', ['P'], 750, 810)]
    result = {b.delivery_id: (lane, count) for b, lane, count in parallel_booking_lanes(items)}
    assert result == {'A': (0, 2), 'B': (1, 2), 'C': (0, 2), 'D': (0, 1)}


@pytest.mark.parametrize('bad', ['duplicate', 'unknown', 'repeated-attendee'])
def test_invalid_presentation_inputs_fail(bad):
    items = [booking('A', ['P'])]
    if bad == 'duplicate': items *= 2
    if bad == 'unknown': items.append(booking('B', ['UNKNOWN']))
    if bad == 'repeated-attendee': items.append(booking('B', ['A', 'A']))
    with pytest.raises(ValueError):
        main_group_timetables(groups(), items)


def test_nested_subgroups_are_in_the_unified_main_view_with_generic_labels():
    rows=groups()
    rows.append(StudentGroup(group_id='A1',parent_group_id='A',student_count=30,
        academic_year=1,semester=1,stream='WEEKDAY'))
    view,=main_group_timetables(rows,[booking('nested-tutorial',['A1'])])
    assert view.labels['A1']=='G1.1.1'
    assert view.bookings[0].delivery_id=='nested-tutorial'
