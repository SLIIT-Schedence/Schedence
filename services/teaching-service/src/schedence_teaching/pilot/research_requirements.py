"""Compulsory student Research is not an invented weekly teaching session.

This is separate from staff research-day protection. Future explicit session
mappings use the existing offering, group, requirement and native solver models.
"""

from collections import Counter, defaultdict
from dataclasses import replace
from urllib.parse import quote

from pydantic import Field, model_validator

from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.preferences import prepare_lecturer_preferences
from schedence_teaching.schemas import GroupMembershipEvidence, ModuleOffering, SessionRequirement, SessionType, TeachingTeamMember, TeachingTimetableInput
from schedence_teaching.schemas.models import InputContract, PositiveInteger, RequiredText
from schedence_teaching.schemas.specializations import specialization_labels
from schedence_teaching.solver import SessionVenueRequirement

PENDING_RESEARCH = "COMPULSORY_RESEARCH_PENDING_SESSION_MAPPING"


class ResearchSessionPattern(InputContract):
    session_type: SessionType
    duration_minutes: PositiveInteger
    sessions_per_week: PositiveInteger
    audience: str = Field(pattern="^(MAIN|LAB_SUBGROUP)$")


class ResearchSessionMapping(InputContract):
    reference: RequiredText
    cohort_ids: tuple[RequiredText, ...] = Field(min_length=1)
    offering: ModuleOffering
    teaching_team_members: tuple[TeachingTeamMember, ...] = Field(min_length=1)
    patterns: tuple[ResearchSessionPattern, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def explicit_mapping(self):
        if self.offering.academic_year != 4 or len(set(self.cohort_ids)) != len(self.cohort_ids):
            raise ValueError("Research mappings need distinct Year 4 cohorts.")
        if any(row.offering_id != self.offering.offering_id for row in self.teaching_team_members):
            raise ValueError("Research eligibility must reference its supplied offering.")
        if len({row.session_type for row in self.patterns}) != len(self.patterns):
            raise ValueError("Use one explicit duration/frequency pattern per Research session type.")
        return self


class ResearchRequirementInput(InputContract):
    policy_reference: RequiredText
    complete_cohort_ids: frozenset[RequiredText] = frozenset()
    session_mappings: tuple[ResearchSessionMapping, ...] = ()


def apply_research_requirements(enrolled, original_batch, inputs):
    """Always retain the obligation; only supplied teaching patterns add sessions."""
    inputs = ResearchRequirementInput.model_validate(inputs.model_dump())
    batch = enrolled.batch
    if batch.metadata.get("research_requirement_applied"):
        raise ValueError("Research requirements were already applied to this enrollment snapshot.")
    cohorts = {row.cohort_id: row for row in batch.plan.cohorts if row.academic_year == 4}
    declared = {identity for mapping in inputs.session_mappings for identity in mapping.cohort_ids}
    if (declared | inputs.complete_cohort_ids) - cohorts.keys() or inputs.complete_cohort_ids - declared:
        raise ValueError("Complete Research mappings need known cohorts and explicit scheduled-session input.")
    raw = batch.prepared.input_snapshot.model_dump()
    groups = list(batch.plan.groups)
    links, specializations = dict(batch.plan.cohort_by_group), dict(batch.group_specializations)
    attendance = {key:set(value) for key,value in enrolled.attendance_by_group.items()}
    memberships = list(enrolled.group_memberships)
    students = defaultdict(list)
    for selection in enrolled.selections:
        students[selection.cohort_id].append(selection.student_id)
    rosters = {row.group_id:row.student_ids for row in memberships}
    additional = defaultdict(list, {key:list(value) for key,value in enrolled.additional_student_requirements.items()})
    source_offerings = dict(batch.source_offerings)
    known_offerings = {row["offering_id"] for row in raw["module_offerings"]}
    mapped_pairs = set()
    for ordinal,mapping in enumerate(inputs.session_mappings,1):
        identity = mapping.offering.offering_id
        if identity in known_offerings:
            raise ValueError("Research offering IDs must not duplicate catalog or other supplied mappings.")
        known_offerings.add(identity)
        raw["module_offerings"].append(mapping.offering.model_dump())
        raw["teaching_team_members"].extend(row.model_dump() for row in mapping.teaching_team_members)
        source_offerings[identity] = dict(workbook="SUPPLIED_RESEARCH_CONTRACT",worksheet="session_mappings JSON array",
            row=ordinal,source_format="JSON_CONTRACT",source_reference=mapping.reference,
            identifier_status="SUPPLIED_RESEARCH_OFFERING_REFERENCE_NOT_ASSUMED_DATABASE_ID")
        for cohort_id in mapping.cohort_ids:
            cohort = cohorts[cohort_id]
            if (cohort.semester,cohort.stream)!=(mapping.offering.semester,mapping.offering.stream) or cohort.specialization not in specialization_labels(mapping.offering.specialization):
                raise ValueError("Research offering and cohort semester, stream and specialization must match.")
            if (cohort_id,identity) in mapped_pairs:
                raise ValueError("A Research mapping may fulfill a cohort/offering only once.")
            mapped_pairs.add((cohort_id,identity))
            cohort_groups = [row for row in original_batch.plan.groups if original_batch.plan.cohort_by_group[row.group_id]==cohort_id]
            main = [row for row in cohort_groups if row.parent_group_id is None]
            ordered,offset = sorted(students[cohort_id]),0
            for parent in main:
                population = tuple(ordered[offset:offset+parent.student_count])
                offset += parent.student_count
                children = ([row for row in cohort_groups if row.parent_group_id==parent.group_id]
                            if any(pattern.audience=="LAB_SUBGROUP" for pattern in mapping.patterns) else [])
                child_offset = 0
                assigned = [(parent,population)]
                for child in children:
                    assigned.append((child,population[child_offset:child_offset+child.student_count]))
                    child_offset += child.student_count
                if children and child_offset!=len(population):
                    raise ValueError("Research lab groups must preserve the supplied cohort partition exactly.")
                for group,population_ids in assigned:
                    if group.group_id not in rosters:
                        if len(groups)>=batch.plan.sizing.maximum_generated_records:
                            raise ValueError("Technical group-record limit exceeded; Research enrollments were not truncated.")
                        groups.append(group)
                        raw["student_groups"].append(group.model_dump())
                        links[group.group_id] = cohort_id
                        specializations[group.group_id] = cohort.specialization
                        rosters[group.group_id] = population_ids
                        memberships.append(GroupMembershipEvidence(group_id=group.group_id,student_ids=population_ids,complete=True,
                            evidence_reference=f"{enrolled.allocation_label}:{inputs.policy_reference}:explicit-cohort-partition"))
                        attendance[group.group_id] = set()
                    attendance[group.group_id].add(identity)
                    for pattern in mapping.patterns:
                        if (group.parent_group_id is None)!=(pattern.audience=="MAIN"):
                            continue
                        requirement = SessionRequirement(requirement_id=f"research:{quote(identity,safe='')}:{quote(group.group_id,safe='')}:{pattern.session_type.value}",
                            offering_id=identity,group_id=group.group_id,session_type=pattern.session_type,
                            duration_minutes=pattern.duration_minutes,sessions_per_week=pattern.sessions_per_week)
                        raw["session_requirements"].append(requirement.model_dump())
                        for student in population_ids:
                            additional[student].append(requirement.model_copy(deep=True))
            if offset!=len(ordered):
                raise ValueError("Research attendance must reconcile to the complete cohort enrollment.")
    teaching = TeachingTimetableInput.model_validate(raw)
    prepared = prepare_timetable_input(teaching)
    contexts = dict(batch.solver_inputs)
    venue = contexts["venue_input"].model_copy(deep=True)
    old_requirements = {row.requirement_id for row in venue.requirements}
    venue.requirements.extend(SessionVenueRequirement(requirement_id=row.requirement_id,allowed_venue_types=batch.configuration.venue_types[row.session_type])
        for row in teaching.session_requirements if row.requirement_id not in old_requirements)
    contexts["venue_input"] = venue
    if "preference_input" in contexts:
        contexts["preference_input"] = prepare_lecturer_preferences(teaching,contexts["preference_input"].input_snapshot)
    if "work_periods" in contexts:
        from schedence_teaching.workload import prepare_work_periods
        contexts["work_periods"] = prepare_work_periods(teaching,contexts["staff_input"].workload_input,contexts["work_periods"].input_snapshot)
    configuration = batch.configuration.model_copy(deep=True)
    configuration.solver_config.attendance_memberships = tuple(memberships)
    configuration.solver_config.attendance_required_group_ids = frozenset(row.group_id for row in memberships)
    configuration = type(configuration).model_validate(configuration.model_dump())
    plan = replace(batch.plan,groups=tuple(groups),cohort_by_group=links,specializations=specializations)
    pending = frozenset(cohorts)-inputs.complete_cohort_ids
    obligations = [dict(cohort_id=identity,academic_year=4,semester=row.semester,stream=row.stream.value,
        specialization=row.specialization,student_count=row.student_count,
        status=PENDING_RESEARCH if identity in pending else "COMPULSORY_RESEARCH_SESSION_MAPPING_SUPPLIED",
        compulsory=True,academic_work_hours=None,academic_work_completed=False,
        weekly_session_minutes_known=sum(pattern.duration_minutes*pattern.sessions_per_week for mapping in inputs.session_mappings
            if identity in mapping.cohort_ids for pattern in mapping.patterns) if identity in inputs.complete_cohort_ids else None)
        for identity,row in sorted(cohorts.items())]
    metadata = dict(batch.metadata,research_requirement_applied=True,research_policy_reference=inputs.policy_reference,
        research_pending_cohort_count=len(pending),research_obligations=obligations,
        research_academic_work_is_not_assumed_zero=True,full_year4_academic_timetable_complete=False,
        session_occurrences=len(prepared.sessions),session_requirements=len(teaching.session_requirements),groups=plan.summary(),
        scoped_local_offering_references=len(teaching.module_offerings),mapped_teaching_team_entries=len(teaching.teaching_team_members),
        session_type_counts=dict(Counter(row.session_type.value for row in prepared.sessions)),
        solver_config=configuration.solver_config.model_dump(mode="json"))
    updated = replace(batch,prepared=prepared,config=configuration.solver_config,configuration=configuration,
        solver_inputs=contexts,plan=plan,source_offerings=source_offerings,group_specializations=specializations,metadata=metadata)
    return replace(enrolled,batch=updated,group_memberships=tuple(memberships),attendance_by_group=attendance,
        additional_student_requirements={key:tuple(value) for key,value in additional.items()},research_pending_cohort_ids=pending)
