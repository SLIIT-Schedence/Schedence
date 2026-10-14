"""Generate optional lecture combinations; preserve all original separate demand."""

from collections import defaultdict
from hashlib import sha256
from itertools import combinations
import json

from pydantic import TypeAdapter, ValidationError

from schedence_teaching.schemas import GroupCombinationInput, PopulationRelationship, SessionType
from schedence_teaching.schemas.models import PositiveInteger

from .combination_population import population_proofs
from .combination_results import (
    CombinationDiagnostic, CombinedLectureOccurrence, GroupCombinationCandidate, PreparedGroupCombinations,
)
from .errors import PreparationError, PreparationIssue
from .sessions import prepare_timetable_input


def _snapshot(inputs: GroupCombinationInput) -> GroupCombinationInput:
    if not isinstance(inputs, GroupCombinationInput):
        raise PreparationError([PreparationIssue("invalid_input", (), "Combination preparation requires GroupCombinationInput.")])
    try:
        return GroupCombinationInput.model_validate(inputs.model_dump(round_trip=True))
    except ValidationError as error:
        raise PreparationError([PreparationIssue(detail["type"], tuple(detail["loc"]), detail["msg"])
                                for detail in error.errors(include_input=False, include_context=False, include_url=False)]) from None


def prepare_group_combinations(inputs: GroupCombinationInput, *, max_candidates: int = 10_000) -> PreparedGroupCombinations:
    """Generate every eligible subset within explicit policy and an operational limit.

    The limit guards exponential alternative growth; exceeding it raises without
    returning a truncated result. It is not a university group-size policy.
    """
    try:
        max_candidates = TypeAdapter(PositiveInteger).validate_python(max_candidates)
    except ValidationError:
        raise PreparationError([PreparationIssue("invalid_generation_limit", ("max_candidates",),
                                                "max_candidates must be a positive strict integer.")]) from None
    inputs = _snapshot(inputs)
    prepared = prepare_timetable_input(inputs.teaching_input)
    proofs = population_proofs(inputs, prepared.hierarchy)
    groups = {group.group_id: group for group in prepared.input_snapshot.student_groups}
    permissions = {row.requirement_id: row for row in inputs.requirement_permissions}
    staff_pools = defaultdict(set)
    for member in prepared.input_snapshot.teaching_team_members:
        staff_pools[member.offering_id].add(member.staff_id)
    diagnostics = []
    active, content = [], {}
    for requirement in sorted(prepared.input_snapshot.session_requirements, key=lambda row: row.requirement_id):
        identity = requirement.requirement_id
        permission = permissions.get(identity)
        allowed = (permission.allow_combination if permission and permission.allow_combination is not None
                   else inputs.policy.allow_combination)
        if not allowed:
            diagnostics.append(CombinationDiagnostic("combination_not_permitted", (identity,), "Separate delivery remains available; combination is not permitted."))
        elif requirement.session_type != SessionType.LECTURE:
            diagnostics.append(CombinationDiagnostic("unsupported_session_type", (identity,), "This foundation generates lecture combinations only."))
        elif requirement.group_id not in inputs.group_specializations:
            diagnostics.append(CombinationDiagnostic("missing_group_specialization", (identity,), "Group specialization must be supplied explicitly, not inferred from the offering."))
        elif not staff_pools[requirement.offering_id]:
            diagnostics.append(CombinationDiagnostic("missing_staff_eligibility", (identity,), "Offering has no explicit eligible staff; no candidate is substituted."))
        else:
            active.append(requirement)
            content[identity] = (
                ("shared_content", permission.shared_content_reference)
                if permission and permission.shared_content_reference is not None else ("offering", requirement.offering_id)
            )
    compatible = set()
    for first_index, second_index in combinations(range(len(active)), 2):
        first, second = active[first_index], active[second_index]
        first_group, second_group = groups[first.group_id], groups[second.group_id]
        pair = tuple(sorted((first.group_id, second.group_id)))
        proof = proofs.get(pair)
        code, message = None, None
        if first.group_id == second.group_id:
            code, message = "same_group", "A group cannot be counted twice in one combined delivery."
        elif any(getattr(first_group, field) != getattr(second_group, field) for field in ("academic_year", "semester", "stream")):
            code, message = "incompatible_period", "Academic year, semester and stream must match."
        elif inputs.group_specializations[first.group_id] != inputs.group_specializations[second.group_id]:
            code, message = "incompatible_specialization", "Groups must have the same explicitly supplied specialization."
        elif content[first.requirement_id] != content[second.requirement_id]:
            code, message = "incompatible_teaching_content", "Matching specialization or module codes does not establish shared lecture content."
        elif first.duration_minutes != second.duration_minutes:
            code, message = "duration_mismatch", "Lecture durations must match; no rounding or shortening is inferred."
        elif first.sessions_per_week != second.sessions_per_week:
            code, message = "frequency_mismatch", "Weekly frequencies must match; missing or extra lectures are not invented."
        elif prepared.hierarchy.conflicts(first.group_id, second.group_id) or (proof and proof.relationship == PopulationRelationship.OVERLAPPING):
            code, message = "overlapping_populations", "Student populations have known overlap and cannot be added as disjoint counts."
        elif proof is None or proof.relationship != PopulationRelationship.DISJOINT:
            code, message = "unknown_population_disjointness", "Different IDs or sibling groups do not prove disjoint populations."
        elif not staff_pools[first.offering_id].intersection(staff_pools[second.offering_id]):
            code, message = "no_common_staff", "No supplied staff member is eligible for both teaching requirements."
        if code:
            diagnostics.append(CombinationDiagnostic(code, (first.requirement_id, second.requirement_id), message))
        else:
            compatible.add((first_index, second_index))

    by_requirement_number = {(session.requirement_id, session.occurrence_number): session for session in prepared.sessions}
    candidates = []
    # Enumerate cliques, pruning as soon as populations/content/staff/policy are incompatible.
    # Every subset is an alternative, never an automatic selection or requirement replacement.
    stack = [((), tuple(range(len(active))), None, 0)]
    maximum_groups = inputs.policy.max_groups if inputs.policy.max_groups is not None else len(active)
    while stack:
        selected, remaining, common_staff, student_count = stack.pop()
        if len(selected) >= 2:
            if len(candidates) == max_candidates:
                raise PreparationError([PreparationIssue("candidate_limit_exceeded", ("max_candidates",),
                    "Eligible alternative count exceeds the operational limit; narrow explicit policy or raise the limit. No partial result is returned.")])
            requirements = tuple(active[index] for index in selected)
            first = requirements[0]
            group = groups[first.group_id]
            refs = tuple(sorted({ref for a, b in combinations(requirements, 2)
                                 for ref in proofs[tuple(sorted((a.group_id, b.group_id)))].references}))
            data = {
                "requirement_ids": tuple(row.requirement_id for row in requirements),
                "group_ids": tuple(row.group_id for row in requirements),
                "offering_ids": tuple(sorted({row.offering_id for row in requirements})),
                "specialization": inputs.group_specializations[first.group_id],
                "academic_year": group.academic_year, "semester": group.semester, "stream": group.stream,
                "content_kind": content[first.requirement_id][0], "content_reference": content[first.requirement_id][1],
                "duration_minutes": first.duration_minutes, "sessions_per_week": first.sessions_per_week,
                "combined_student_count": student_count, "eligible_staff_ids": tuple(sorted(common_staff)),
                "population_evidence_references": refs,
            }
            digest = sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            candidate_id = "lecture-combination:" + digest
            occurrences = tuple(CombinedLectureOccurrence(
                f"{candidate_id}:{number}", number,
                tuple(by_requirement_number[(row.requirement_id, number)] for row in requirements),
                student_count, data["eligible_staff_ids"],
            ) for number in range(1, first.sessions_per_week + 1))
            candidates.append(GroupCombinationCandidate(candidate_id=candidate_id, occurrences=occurrences, **data))
        if len(selected) >= maximum_groups:
            continue
        for index in reversed(remaining):
            requirement = active[index]
            new_selected = selected + (index,)
            shared = staff_pools[requirement.offering_id] if common_staff is None else common_staff.intersection(staff_pools[requirement.offering_id])
            count = student_count + groups[requirement.group_id].student_count
            reason = ("no_common_staff" if not shared else "student_count_limit"
                      if inputs.policy.max_combined_students is not None and count > inputs.policy.max_combined_students else None)
            if reason:
                diagnostics.append(CombinationDiagnostic(reason, tuple(active[i].requirement_id for i in new_selected),
                    "No staff member is eligible for every offering in this subset." if reason == "no_common_staff"
                    else "Combined demand exceeds the explicitly supplied max_combined_students policy."))
                continue
            extensions = tuple(other for other in remaining if other > index
                               and all((chosen, other) in compatible for chosen in new_selected))
            stack.append((new_selected, extensions, shared, count))
    candidates.sort(key=lambda candidate: candidate.requirement_ids)
    diagnostics.sort(key=lambda row: (row.requirement_ids, row.code))
    return PreparedGroupCombinations(inputs, prepared, tuple(candidates), tuple(diagnostics), max_candidates)
