"""Positive population evidence, never inferred from group names or siblinghood."""

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

from schedence_teaching.schemas import GroupCombinationInput, PopulationRelationship

from .errors import PreparationError, PreparationIssue
from .hierarchy import GroupHierarchy


@dataclass(frozen=True)
class PopulationProof:
    relationship: PopulationRelationship
    references: tuple[str, ...]


def population_proofs(inputs: GroupCombinationInput, hierarchy: GroupHierarchy) -> dict[tuple[str, str], PopulationProof]:
    assertions = defaultdict(lambda: defaultdict(set))
    for evidence in inputs.population_evidence:
        for pair in combinations(sorted(evidence.group_ids), 2):
            references = assertions[pair][evidence.relationship]
            if evidence.evidence_reference is not None:
                references.add(evidence.evidence_reference)
    for first, second in combinations(sorted(inputs.memberships, key=lambda row: row.group_id), 2):
        pair = (first.group_id, second.group_id)
        if set(first.student_ids).intersection(second.student_ids):
            relationship = PopulationRelationship.OVERLAPPING
        elif first.complete and second.complete:
            relationship = PopulationRelationship.DISJOINT
        else:
            relationship = PopulationRelationship.UNKNOWN
        assertions[pair][relationship].update((first.evidence_reference, second.evidence_reference))
    issues, proofs = [], {}
    for pair, relationships in sorted(assertions.items()):
        disjoint = PopulationRelationship.DISJOINT in relationships
        overlapping = PopulationRelationship.OVERLAPPING in relationships or hierarchy.conflicts(*pair)
        if disjoint and overlapping:
            issues.append(PreparationIssue(
                "conflicting_population_evidence", ("population_evidence",),
                f"Population evidence contradicts another record or the hierarchy for groups {pair[0]} and {pair[1]}.",
            ))
            continue
        status = (PopulationRelationship.OVERLAPPING if overlapping else
                  PopulationRelationship.DISJOINT if disjoint else PopulationRelationship.UNKNOWN)
        proofs[pair] = PopulationProof(status, tuple(sorted(relationships.get(status, ()))))
    if issues:
        raise PreparationError(issues)
    return proofs
