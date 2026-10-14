"""Forest indexing for the group relationships explicitly supplied by input."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from schedence_teaching.schemas import StudentGroup

from .errors import PreparationError, PreparationIssue
from .validation import period_issues, validated_group_snapshot


@dataclass(frozen=True)
class GroupHierarchy:
    parents: Mapping[str, str | None]
    children: Mapping[str, tuple[str, ...]]
    _intervals: Mapping[str, tuple[int, int]]
    _preorder: tuple[str, ...]

    @property
    def main_group_ids(self) -> tuple[str, ...]:
        return tuple(group_id for group_id, parent in self.parents.items() if parent is None)

    @property
    def subgroup_ids(self) -> tuple[str, ...]:
        return tuple(group_id for group_id, parent in self.parents.items() if parent is not None)

    def _require_group(self, group_id: str) -> None:
        if group_id not in self.parents:
            raise PreparationError([PreparationIssue(
                "unknown_group", ("student_groups",), f"Unknown group_id: {group_id}",
            )])

    def descendants(self, group_id: str) -> tuple[str, ...]:
        self._require_group(group_id)
        start, end = self._intervals[group_id]
        return self._preorder[start + 1:end]

    def conflicts(self, first_id: str, second_id: str) -> bool:
        """True means a known hierarchy conflict; False does not prove disjoint students."""
        self._require_group(first_id)
        self._require_group(second_id)
        first_start, first_end = self._intervals[first_id]
        second_start, second_end = self._intervals[second_id]
        return first_start <= second_start < first_end or second_start <= first_start < second_end


def _build_hierarchy(groups: Sequence[StudentGroup]) -> GroupHierarchy:
    """Inputs have already passed the shared parent-existence/cycle validation."""
    by_id = {group.group_id: group for group in groups}
    issues = []
    for index, group in enumerate(groups):
        if group.parent_group_id is not None:
            parent = by_id[group.parent_group_id]
            issues.extend(period_issues(
                group, parent, code="parent_period_mismatch", location=("student_groups", index),
                description=f"Child {group.group_id} and parent {parent.group_id}",
            ))
    if issues:
        raise PreparationError(issues)

    parents = {group_id: by_id[group_id].parent_group_id for group_id in sorted(by_id)}
    children = {group_id: [] for group_id in parents}
    for group_id, parent in parents.items():
        if parent is not None:
            children[parent].append(group_id)
    roots = [group_id for group_id, parent in parents.items() if parent is None]
    preorder: list[str] = []
    starts: dict[str, int] = {}
    intervals: dict[str, tuple[int, int]] = {}
    stack = [(root, False) for root in reversed(roots)]
    # Iterative traversal supports nested hierarchies without recursion-depth limits.
    while stack:
        group_id, closing = stack.pop()
        if closing:
            intervals[group_id] = (starts[group_id], len(preorder))
        else:
            starts[group_id] = len(preorder)
            preorder.append(group_id)
            stack.append((group_id, True))
            stack.extend((child, False) for child in reversed(children[group_id]))
    return GroupHierarchy(
        MappingProxyType(parents),
        MappingProxyType({group_id: tuple(items) for group_id, items in children.items()}),
        MappingProxyType(intervals), tuple(preorder),
    )


def build_group_hierarchy(groups: Sequence[StudentGroup]) -> GroupHierarchy:
    """Validate a supplied group forest without resizing or partitioning groups."""
    return _build_hierarchy(validated_group_snapshot(groups).student_groups)


def groups_conflict(hierarchy: GroupHierarchy, first_id: str, second_id: str) -> bool:
    return hierarchy.conflicts(first_id, second_id)
