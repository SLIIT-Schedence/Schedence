"""Explicit Year 4 enrollment, separate from specialization applicability.

Classifications are supplied input. Test registrations use local identifiers and
never assert actual university enrollment. Complete rosters carry overlap proof.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from enum import StrEnum
from hashlib import sha256
from random import Random
from typing import Literal

from pydantic import Field, model_validator

from schedence_teaching.excel_import.staging import SourceLocation
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.preferences import prepare_lecturer_preferences
from schedence_teaching.schemas import GroupMembershipEvidence, SessionRequirement, SessionType, StudentGroup
from schedence_teaching.schemas.models import InputContract, RequiredText

from .academic import CONTACT_HOUR_COLUMNS, OfferingSourceBinding, NonLecturePattern, build_specialization_teaching_input, extract_academic_catalog
from .students import PlannedGroups, balanced_sizes


class ModuleKind(StrEnum):
    UNKNOWN = "UNKNOWN"
    COMPULSORY = "COMPULSORY"
    ELECTIVE = "ELECTIVE"
    PROJECT = "PROJECT"


class ModuleClassification(InputContract):
    source: SourceLocation
    specialization: RequiredText
    kind: ModuleKind
    reference: RequiredText


class ElectiveSelection(InputContract):
    cohort_id: RequiredText
    student_id: RequiredText
    offering_ids: tuple[RequiredText, RequiredText]

    @model_validator(mode="after")
    def two_distinct(self):
        if len(set(self.offering_ids)) != 2:
            raise ValueError("Each student must select exactly two distinct elective offerings.")
        return self


class ElectiveAllocationPolicy(InputContract):
    output_label: Literal["PP1_TEST_ELECTIVE_ALLOCATION"] = "PP1_TEST_ELECTIVE_ALLOCATION"
    reference: RequiredText
    random_seed: int = Field(strict=True, ge=0)
    selection_strategy: Literal["RANDOM_ELIGIBLE_PAIRS", "BALANCED_TEST_PAIRS"] = "RANDOM_ELIGIBLE_PAIRS"
    # Optional restrictions are explicit, never inferred from module names/codes.
    allowed_pairs_by_cohort: dict[RequiredText, tuple[tuple[RequiredText, RequiredText], ...]] = Field(default_factory=dict)


class ElectiveInputError(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = tuple(diagnostics)
        super().__init__("Year 4 enrollment input is incomplete or invalid; inspect classification/enrollment diagnostics.")


def _location(row):
    return SourceLocation(row["workbook"], row["worksheet"], row["row"])


def classification_template(academic):
    """Only explicit source labels supply classifications; other rows stay unknown."""
    rows = []
    for module in extract_academic_catalog(academic).modules:
        if module.source.contract_fields["academic_year"] != 4:
            continue
        explicit = "(Elective)" in module.source.contract_fields["module_name"]
        for specialization in module.specializations:
            rows.append(ModuleClassification(source=module.source.location, specialization=specialization,
                kind="ELECTIVE" if explicit else "UNKNOWN", reference="source:Module_Name:explicit-elective-label" if explicit else "classification-not-supplied"))
    return rows


def confirmed_year4_candidate_catalog(academic, *, reference, overrides=()):
    """PP1 candidate pool under the user's two-electives-plus-Research rule.

    This is separate policy metadata, never a newly discovered workbook column.
    Explicit compulsory/project classifications always survive overrides.
    """
    rows = {(row.source,row.specialization): row.model_copy(update=dict(kind=ModuleKind.ELECTIVE,reference=reference))
            for row in classification_template(academic)}
    seen = set()
    for original in overrides:
        row = ModuleClassification.model_validate(original.model_dump() if isinstance(original,ModuleClassification) else original)
        key = (row.source,row.specialization)
        if key not in rows or key in seen:
            raise ValueError("Classification override must uniquely reference an existing Year 4 candidate entry.")
        seen.add(key)
        rows[key] = row
    return tuple(rows.values())


def assess_elective_catalog(batch, academic, classifications):
    catalog = extract_academic_catalog(academic)
    if not catalog.valid:
        raise ValueError("A validated academic catalog is required.")
    modules = {row.source.location: row for row in catalog.modules}
    classified = {}
    for original in classifications:
        row = ModuleClassification.model_validate(original.model_dump() if isinstance(original, ModuleClassification) else original)
        key = (row.source, row.specialization)
        if key in classified:
            raise ValueError("Duplicate source-row/specialization classification.")
        module = modules.get(row.source)
        if module is None or module.source.contract_fields["academic_year"] != 4 or row.specialization not in module.specializations:
            raise ValueError("Classification must reference an applicable Year 4 source row and specialization.")
        classified[key] = row
    offerings = {row.offering_id: row for row in batch.prepared.input_snapshot.module_offerings}
    cohorts, records, diagnostics = {}, [], []
    for cohort in batch.plan.cohorts:
        if cohort.academic_year != 4:
            continue
        applicable = []
        for identity, offering in offerings.items():
            location = _location(batch.source_offerings[identity])
            module = modules[location]
            if (offering.academic_year, offering.semester, offering.stream) != (4, cohort.semester, cohort.stream) or cohort.specialization not in module.specializations:
                continue
            classification = classified.get((location, cohort.specialization))
            kind = classification.kind if classification else ModuleKind.UNKNOWN
            applicable.append((identity, kind, module))
            records.append(dict(cohort_id=cohort.cohort_id, academic_year=4, semester=cohort.semester,
                stream=cohort.stream.value, specialization=cohort.specialization, source_workbook=location.workbook,
                source_worksheet=location.worksheet, source_row=location.row, module_code=offering.module_code,
                module_name=offering.module_name, offering_id=identity, identifier_status="LOCAL_PP1_SOURCE_ROW_REFERENCE_NOT_DATABASE_ID",
                classification=kind.value, classification_reference=classification.reference if classification else "classification-not-supplied",
                **{column: module.source.source_values[column] for column in CONTACT_HOUR_COLUMNS.values()},
                weekly_contact_minutes=sum(module.weekly_minutes.values())))
        electives = {identity for identity, kind, _ in applicable if kind == ModuleKind.ELECTIVE}
        unknown = [module.source.location.row for _, kind, module in applicable if kind == ModuleKind.UNKNOWN]
        if unknown:
            diagnostics.append(dict(code="MISSING_MODULE_CLASSIFICATION", cohort_id=cohort.cohort_id, source_rows=unknown))
        if len(electives) < 2:
            diagnostics.append(dict(code="FEWER_THAN_TWO_IDENTIFIED_ELECTIVES", cohort_id=cohort.cohort_id, eligible_count=len(electives), applicable_source_rows=len(applicable)))
        cohorts[cohort.cohort_id] = dict(cohort=cohort, electives=electives,
            required={identity for identity, kind, _ in applicable if kind in {ModuleKind.COMPULSORY, ModuleKind.PROJECT}}, applicable=applicable)
    if not cohorts:
        diagnostics.append(dict(code="NO_YEAR4_COHORTS_IN_REQUEST"))
    return dict(cohorts=cohorts, catalog=records, diagnostics=diagnostics, valid=not diagnostics)


def _allowed_pairs(policy, assessed):
    unknown = set(policy.allowed_pairs_by_cohort) - set(assessed["cohorts"])
    if unknown:
        raise ValueError("Pair restrictions reference an unknown Year 4 cohort.")
    result = {}
    for identity, pairs in policy.allowed_pairs_by_cohort.items():
        eligible = assessed["cohorts"][identity]["electives"]
        normalized = [tuple(sorted(pair)) for pair in pairs]
        if not normalized or len(set(normalized)) != len(normalized) or any(len(set(pair)) != 2 or not set(pair) <= eligible for pair in normalized):
            raise ValueError("Allowed pairs must be nonempty, distinct pairs of eligible electives.")
        result[identity] = tuple(sorted(normalized))
    return result


def generate_test_electives(batch, academic, classifications, policy):
    policy = ElectiveAllocationPolicy.model_validate(policy.model_dump())
    assessed = assess_elective_catalog(batch, academic, classifications)
    if not assessed["valid"]:
        raise ElectiveInputError(assessed["diagnostics"])
    pairs = _allowed_pairs(policy, assessed)
    selections = []
    for identity, data in sorted(assessed["cohorts"].items()):
        # Cohort-local seed makes unrelated cohorts/order changes harmless.
        seed = int.from_bytes(sha256(f"{policy.random_seed}:{identity}".encode()).digest(), "big")
        random = Random(seed)
        choices = sorted(data["electives"])
        balanced_pairs = None
        if policy.selection_strategy == "BALANCED_TEST_PAIRS":
            if identity in pairs:
                balanced_pairs = list(pairs[identity])
                random.shuffle(balanced_pairs)
            else:
                random.shuffle(choices)
                balanced_pairs = [tuple(sorted((choices[index],choices[(index+1)%len(choices)])))
                                  for index in range(0,len(choices),2)]
        for ordinal in range(1, data["cohort"].student_count + 1):
            selected = (balanced_pairs[(ordinal-1)%len(balanced_pairs)] if balanced_pairs is not None else
                random.choice(pairs[identity]) if identity in pairs else tuple(sorted(random.sample(choices, 2))))
            selections.append(ElectiveSelection(cohort_id=identity, student_id=f"PP1_TEST_STUDENT:{identity}:{ordinal:05}", offering_ids=selected))
    return tuple(selections)


def validate_elective_selections(batch, academic, classifications, selections, *, policy=None):
    if policy is not None:
        policy = ElectiveAllocationPolicy.model_validate(policy.model_dump())
    assessed = assess_elective_catalog(batch, academic, classifications)
    if not assessed["valid"]:
        raise ElectiveInputError(assessed["diagnostics"])
    pairs = _allowed_pairs(policy, assessed) if policy is not None else {}
    validated, seen, counts, issues = [], set(), Counter(), []
    for original in selections:
        row = ElectiveSelection.model_validate(original.model_dump() if isinstance(original, ElectiveSelection) else original)
        data = assessed["cohorts"].get(row.cohort_id)
        if data is None or not set(row.offering_ids) <= data["electives"]:
            issues.append(dict(code="INELIGIBLE_ELECTIVE_SELECTION", cohort_id=row.cohort_id))
        if row.student_id in seen:
            issues.append(dict(code="DUPLICATE_STUDENT_REGISTRATION", cohort_id=row.cohort_id))
        if row.cohort_id in pairs and tuple(sorted(row.offering_ids)) not in pairs[row.cohort_id]:
            issues.append(dict(code="DISALLOWED_ELECTIVE_PAIR", cohort_id=row.cohort_id))
        seen.add(row.student_id)
        counts[row.cohort_id] += 1
        validated.append(row)
    for identity, data in assessed["cohorts"].items():
        if counts[identity] != data["cohort"].student_count:
            issues.append(dict(code="COHORT_STUDENT_COUNT_MISMATCH", cohort_id=identity,
                expected=data["cohort"].student_count, supplied=counts[identity]))
    if issues:
        raise ElectiveInputError(issues)
    return tuple(validated), assessed


@dataclass(frozen=True)
class EnrollmentPlan(PlannedGroups):
    def summary(self):
        result = super().summary()
        result.update(total_students=sum(row.student_count for row in self.cohorts),
            main_student_sum_is_attendance_enrollments_not_unique_students=True,
            enrollment_count_scope="SUM_OF_DISTINCT_COHORT_COUNTS_NOT_A_CROSS_SEMESTER_PERSON_DEDUPLICATION")
        return result


@dataclass(frozen=True)
class EnrolledPP1:
    batch: object
    selections: tuple[ElectiveSelection, ...]
    classifications: tuple[ModuleClassification, ...]
    group_memberships: tuple[GroupMembershipEvidence, ...]
    attendance_by_group: dict[str, set[str]]
    catalog_assessment: dict
    allocation_label: str
    additional_student_requirements: dict[str, tuple[SessionRequirement, ...]] = field(default_factory=dict)
    research_pending_cohort_ids: frozenset[str] = frozenset()


def prepare_enrolled_pp1(base, academic, classifications, selections, *, policy=None,
                         allocation_label="PP1_TEST_ELECTIVE_ALLOCATION"):
    if allocation_label not in {"PP1_TEST_ELECTIVE_ALLOCATION", "SUPPLIED_ELECTIVE_REGISTRATIONS"}:
        raise ValueError("Explicit registration provenance is required.")
    if base.plan.sizing.main_group_maximum > 120 or base.plan.sizing.lab_subgroup_maximum > 60:
        raise ValueError("PP1 elective attendance must respect the requested main-120/lab-60 maximums.")
    classifications = tuple(ModuleClassification.model_validate(row.model_dump() if isinstance(row, ModuleClassification) else row) for row in classifications)
    selections, assessed = validate_elective_selections(base, academic, classifications, selections, policy=policy)
    if allocation_label == "PP1_TEST_ELECTIVE_ALLOCATION" and (policy is None or any(not row.student_id.startswith("PP1_TEST_STUDENT:") for row in selections)):
        raise ValueError("Generated test registrations require explicit policy and test-only student IDs.")
    catalog = extract_academic_catalog(academic)
    modules = {row.source.location: row for row in catalog.modules}
    by_cohort = defaultdict(list)
    for row in selections:
        by_cohort[row.cohort_id].append(row)
    groups = [row for row in base.plan.groups if row.academic_year != 4]
    links = {row.group_id: base.plan.cohort_by_group[row.group_id] for row in groups}
    specializations = {row.group_id: base.group_specializations[row.group_id] for row in groups}
    attendance = {row.group_id: set() for row in groups}
    for row in base.prepared.sessions:
        if row.group_id in attendance:
            attendance[row.group_id].add(row.offering_id)
    memberships = []
    for cohort_id, data in sorted(assessed["cohorts"].items()):
        cohort = data["cohort"]
        enrolled = defaultdict(set)
        for selection in by_cohort[cohort_id]:
            for identity in {*selection.offering_ids, *data["required"]}:
                enrolled[identity].add(selection.student_id)
        for identity, students in sorted(enrolled.items()):
            ordered, offset = sorted(students), 0
            prefix = sha256(f"{cohort_id}:{identity}".encode()).hexdigest()[:24]
            module = modules[_location(base.source_offerings[identity])]
            needs_subgroups = any(minutes and base.configuration.session_audiences[kind]=="LAB_SUBGROUP" for kind,minutes in module.weekly_minutes.items())
            for ordinal, size in enumerate(balanced_sizes(len(ordered), base.plan.sizing.main_group_maximum), 1):
                main_students = ordered[offset:offset+size]
                offset += size
                parent = f"PP1-ENROLL-{prefix}-G{ordinal:03}"
                common = dict(academic_year=4, semester=cohort.semester, stream=cohort.stream)
                main = StudentGroup(group_id=parent, student_count=size, **common)
                records = [(main, main_students)]
                child_offset = 0
                child_sizes = balanced_sizes(size, base.plan.sizing.lab_subgroup_maximum) if needs_subgroups else ()
                for child_ordinal, child_size in enumerate(child_sizes, 1):
                    child = StudentGroup(group_id=f"{parent}.L{child_ordinal:02}", parent_group_id=parent, student_count=child_size, **common)
                    records.append((child, main_students[child_offset:child_offset+child_size]))
                    child_offset += child_size
                for group, roster in records:
                    if len(groups) >= base.plan.sizing.maximum_generated_records:
                        raise ValueError("Technical group-record limit exceeded; no enrollments were truncated.")
                    groups.append(group)
                    links[group.group_id] = cohort_id
                    specializations[group.group_id] = cohort.specialization
                    attendance[group.group_id] = {identity}
                    memberships.append(GroupMembershipEvidence(group_id=group.group_id, student_ids=tuple(roster), complete=True,
                        evidence_reference=f"{allocation_label}:{policy.reference if policy else 'supplied-registration-file'}"))
    active = set().union(*attendance.values())
    bindings, patterns = [], []
    for offering in base.prepared.input_snapshot.module_offerings:
        if offering.offering_id not in active:
            continue
        location = _location(base.source_offerings[offering.offering_id])
        module = modules[location]
        bindings.append(OfferingSourceBinding(source=location, offering=offering))
        for kind, minutes in module.weekly_minutes.items():
            if kind != SessionType.LECTURE and minutes:
                patterns.append(NonLecturePattern(offering_id=offering.offering_id, session_type=kind,
                    duration_minutes=minutes, sessions_per_week=1,
                    source_reference=base.configuration.configuration_reference+":unsplit-contact-column"))
    audiences = {kind: tuple(row.group_id for row in groups if (row.parent_group_id is None) == (audience == "MAIN"))
                 for kind, audience in base.configuration.session_audiences.items()}
    teaching = build_specialization_teaching_input(academic, catalog, offerings=bindings, groups=groups,
        group_specializations=specializations, session_audiences=audiences, nonlecture_patterns=patterns,
        module_attendance=attendance)
    prepared = prepare_timetable_input(teaching)
    # Compare directly by original group IDs; generated Year 4 groups have new IDs.
    old_ids = {row.group_id for row in base.plan.groups if row.academic_year != 4}
    if tuple(row for row in prepared.sessions if row.group_id in old_ids) != tuple(row for row in base.prepared.sessions if row.group_id in old_ids):
        raise ValueError("Enrollment correction must preserve every non-Year-4 occurrence exactly.")
    contexts = dict(base.solver_inputs)
    venues = contexts["venue_input"].model_copy(deep=True)
    from schedence_teaching.solver import SessionVenueRequirement
    venues.requirements = [SessionVenueRequirement(requirement_id=row.requirement_id, allowed_venue_types=base.configuration.venue_types[row.session_type]) for row in teaching.session_requirements]
    venues = type(venues).model_validate(venues.model_dump())
    contexts["venue_input"] = venues
    if "preference_input" in contexts:
        contexts["preference_input"] = prepare_lecturer_preferences(teaching, contexts["preference_input"].input_snapshot)
    if "work_periods" in contexts:
        from schedence_teaching.workload import prepare_work_periods
        contexts["work_periods"] = prepare_work_periods(teaching, contexts["staff_input"].workload_input, contexts["work_periods"].input_snapshot)
    configuration = base.configuration.model_copy(deep=True)
    configuration.solver_config.attendance_memberships = tuple(memberships)
    configuration.solver_config.attendance_required_group_ids = frozenset(row.group_id for row in memberships)
    configuration = type(configuration).model_validate(configuration.model_dump())
    plan = EnrollmentPlan(base.plan.cohorts, tuple(groups), links, specializations, base.plan.sizing)
    metadata = dict(base.metadata, groups=plan.summary(), session_occurrences=len(prepared.sessions),
        session_requirements=len(teaching.session_requirements), scoped_local_offering_references=len(active),
        mapped_teaching_team_entries=len(teaching.teaching_team_members),
        session_type_counts=dict(Counter(row.session_type.value for row in prepared.sessions)),
        original_catalog_application_occurrences=len(base.prepared.sessions),
        allocation_label=allocation_label, year4_registered_students=len(selections),
        simulated_year4_students=len(selections) if allocation_label=="PP1_TEST_ELECTIVE_ALLOCATION" else 0,
        elective_registration_entries=2*len(selections), confirmed_electives_per_student=2,
        elective_membership_conflicts_enabled=True, shared_lecture_combining=False,
        complete_test_population_membership_supplied=allocation_label=="PP1_TEST_ELECTIVE_ALLOCATION",
        solver_config=configuration.solver_config.model_dump(mode="json"))
    if "selected_complete_scopes" in metadata:
        metadata["catalog_application_outside_scope_occurrences"] = metadata.get("out_of_scope_occurrences")
        metadata["out_of_scope_occurrences"] = None  # Other cohorts' corrected enrollment demand is not known here.
        metadata.pop("original_global_occurrences", None)
        metadata["scope_completeness"] = "ALL_REGISTERED_REQUIREMENTS_FOR_SELECTED_COHORTS"
    updated = replace(base, prepared=prepared, config=configuration.solver_config, configuration=configuration,
        solver_inputs=contexts, plan=plan, group_specializations=specializations,
        source_offerings={identity: base.source_offerings[identity] for identity in active}, metadata=metadata)
    return EnrolledPP1(updated, selections, tuple(classifications), tuple(memberships), attendance, assessed, allocation_label)


def select_enrolled_scope(enrolled, scopes, *, max_time_seconds=None):
    from .scopes import select_complete_scope
    batch = select_complete_scope(enrolled.batch, scopes, max_time_seconds=max_time_seconds, retain_applicable_catalog=True)
    cohorts = {row.cohort_id for row in batch.plan.cohorts}
    if batch.metadata.get("research_requirement_applied"):
        batch=replace(batch,metadata=dict(batch.metadata,research_pending_cohort_count=len(enrolled.research_pending_cohort_ids & cohorts),
            research_obligations=[row for row in batch.metadata["research_obligations"] if row["cohort_id"] in cohorts]))
    return replace(enrolled, batch=batch, selections=tuple(row for row in enrolled.selections if row.cohort_id in cohorts),
        group_memberships=tuple(row for row in enrolled.group_memberships if row.group_id in batch.group_specializations),
        attendance_by_group={key: value for key,value in enrolled.attendance_by_group.items() if key in batch.group_specializations},
        additional_student_requirements={key: value for key,value in enrolled.additional_student_requirements.items()
            if key in {row.student_id for row in enrolled.selections if row.cohort_id in cohorts}},
        research_pending_cohort_ids=enrolled.research_pending_cohort_ids & cohorts,
        catalog_assessment=dict(enrolled.catalog_assessment,
            cohorts={key: value for key,value in enrolled.catalog_assessment["cohorts"].items() if key in cohorts},
            catalog=[row for row in enrolled.catalog_assessment["catalog"] if row["cohort_id"] in cohorts]))
