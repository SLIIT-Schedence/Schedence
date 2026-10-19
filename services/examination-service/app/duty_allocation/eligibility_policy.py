"""Configurable staff eligibility rules for Supervisor and Invigilator duties.

Nothing here is SLIIT policy. The institution supplies the academic rank order, the
minimum rank (if any) for each duty role, whether staff without a rank may take a role,
named restrictions, and the minimum gap between one person's duties. There is no
default policy: every rule must be configured explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.duty_allocation.types import DUTY_INPUT, DutyRole, is_staff_uuid
from app.scheduling.contracts import SchedulingValidationError, Violation, frozen_sequence


def _fail(message: str) -> None:
    raise SchedulingValidationError((Violation(DUTY_INPUT, message),))


def normalize_rank(rank: str | None) -> str | None:
    """Compare ranks case- and whitespace-insensitively; blank means no rank."""
    if rank is None:
        return None
    text = " ".join(rank.split()).casefold()
    return text or None


def _optional_text(value: object, label: str) -> None:
    if value is not None and (type(value) is not str or not value.strip()):
        _fail(f"{label} must be None or a non-empty string.")


@dataclass(frozen=True)
class RoleRule:
    role: DutyRole
    minimum_rank: str | None  # None: rank is not checked for this role
    allow_unranked: bool  # may staff with no academic_rank take this role?

    def __post_init__(self) -> None:
        if type(self.role) is not DutyRole:
            _fail("role rule role must be a DutyRole.")
        _optional_text(self.minimum_rank, f"{self.role.value} minimum_rank")
        if type(self.allow_unranked) is not bool:
            _fail(f"{self.role.value} allow_unranked must be a bool.")


@dataclass(frozen=True)
class StaffRestriction:
    """Excludes one staff member from duties. Unset role/session/module match everything,
    so a restriction with only staff_id excludes that person from all duties."""
    staff_id: str
    reason: str
    role: DutyRole | None = None
    session_id: str | None = None
    module_code: str | None = None

    def __post_init__(self) -> None:
        if not is_staff_uuid(self.staff_id):
            _fail("restriction staff_id must be a lowercase UUID string.")
        if type(self.reason) is not str or not self.reason.strip():
            _fail("restriction reason must be a non-empty string.")
        if self.role is not None and type(self.role) is not DutyRole:
            _fail("restriction role must be None or a DutyRole.")
        _optional_text(self.session_id, "restriction session_id")
        _optional_text(self.module_code, "restriction module_code")

    def applies(self, staff_id: str, role: DutyRole, session_id: str, module_code: str) -> bool:
        return (self.staff_id == staff_id and self.role in (None, role)
                and self.session_id in (None, session_id) and self.module_code in (None, module_code))


@dataclass(frozen=True)
class EligibilityPolicy:
    rank_order: tuple[str, ...]  # most senior first
    role_rules: tuple[RoleRule, ...]  # exactly one per DutyRole
    minimum_gap_minutes: int  # between one person's consecutive duties
    source: str
    restrictions: tuple[StaffRestriction, ...] = ()

    def __post_init__(self) -> None:
        ranks = frozen_sequence(self.rank_order, "rank_order", str)
        normalized = [normalize_rank(r) for r in ranks]
        if None in normalized or len(set(normalized)) != len(normalized):
            _fail("rank_order must contain unique, non-blank ranks.")
        rules = frozen_sequence(self.role_rules, "role_rules", RoleRule)
        if sorted(r.role.value for r in rules) != sorted(r.value for r in DutyRole):
            _fail("role_rules must configure each duty role exactly once.")
        for rule in rules:
            if rule.minimum_rank is not None and normalize_rank(rule.minimum_rank) not in normalized:
                _fail(f"{rule.role.value} minimum_rank {rule.minimum_rank!r} is not in rank_order.")
        if type(self.minimum_gap_minutes) is not int or self.minimum_gap_minutes < 0:
            _fail("minimum_gap_minutes must be a non-negative int.")
        if type(self.source) is not str or not self.source.strip():
            _fail("eligibility policy source must be a non-empty string.")
        object.__setattr__(self, "rank_order", ranks)
        object.__setattr__(self, "role_rules", rules)
        object.__setattr__(self, "restrictions", frozen_sequence(self.restrictions, "restrictions", StaffRestriction))

    def rule(self, role: DutyRole) -> RoleRule:
        return next(r for r in self.role_rules if r.role is role)

    def seniority(self, rank: str | None) -> int | None:
        """0 is the most senior configured rank; None for no rank or an unconfigured one."""
        key = normalize_rank(rank)
        normalized = [normalize_rank(r) for r in self.rank_order]
        return normalized.index(key) if key in normalized else None

    def rank_decision(self, rank: str | None, role: DutyRole) -> str | None:
        """None if the rank qualifies for the role, otherwise the reason it does not."""
        rule = self.rule(role)
        if normalize_rank(rank) is None:
            return None if rule.allow_unranked else f"no academic rank; {role.value} requires one"
        if rule.minimum_rank is None:
            return None
        level = self.seniority(rank)
        if level is None:
            return f"academic rank {rank!r} is not in the configured rank order"
        if level > self.seniority(rule.minimum_rank):
            return f"academic rank {rank!r} is below the {role.value} minimum {rule.minimum_rank!r}"
        return None

    def roles_for_rank(self, rank: str | None) -> tuple[tuple[DutyRole, ...], tuple[str, ...]]:
        decisions = [(role, self.rank_decision(rank, role)) for role in DutyRole]
        return (tuple(role for role, reason in decisions if reason is None),
                tuple(reason for _, reason in decisions if reason is not None))

    def restriction_for(self, staff_id: str, role: DutyRole, session_id: str, module_code: str) -> StaffRestriction | None:
        return next((r for r in self.restrictions if r.applies(staff_id, role, session_id, module_code)), None)

    @classmethod
    def from_mapping(cls, data: Mapping) -> EligibilityPolicy:
        """Build from configuration, e.g. parsed JSON:
        {"source": "...", "rank_order": ["Rank A", "Rank B"], "minimum_gap_minutes": 0,
         "roles": {"SUPERVISOR": {"minimum_rank": "Rank A", "allow_unranked": false},
                   "INVIGILATOR": {"minimum_rank": null, "allow_unranked": true}},
         "restrictions": [{"staff_id": "<uuid>", "reason": "...", "role": null,
                           "session_id": null, "module_code": "IT1010"}]}"""
        if not isinstance(data, Mapping):
            _fail("eligibility config must be a mapping.")
        required = {"source", "rank_order", "minimum_gap_minutes", "roles"}
        if not required <= set(data) or set(data) - required - {"restrictions"}:
            _fail("eligibility config needs exactly source, rank_order, minimum_gap_minutes, roles (restrictions optional).")
        roles = data["roles"]
        if not isinstance(roles, Mapping) or set(roles) != {r.value for r in DutyRole}:
            _fail("eligibility config roles must configure SUPERVISOR and INVIGILATOR.")
        rules = []
        for name, row in roles.items():
            if not isinstance(row, Mapping) or set(row) != {"minimum_rank", "allow_unranked"}:
                _fail(f"eligibility config roles.{name} needs exactly minimum_rank and allow_unranked.")
            rules.append(RoleRule(DutyRole(name), row["minimum_rank"], row["allow_unranked"]))
        restriction_keys = {"staff_id", "reason", "role", "session_id", "module_code"}
        restrictions = []
        for index, row in enumerate(data.get("restrictions") or ()):
            if not isinstance(row, Mapping) or not {"staff_id", "reason"} <= set(row) or set(row) - restriction_keys:
                _fail(f"eligibility config restrictions[{index}] has missing or unknown fields.")
            role = row.get("role")
            if role is not None and role not in {r.value for r in DutyRole}:
                _fail(f"eligibility config restrictions[{index}].role is not a duty role.")
            restrictions.append(StaffRestriction(row["staff_id"], row["reason"], DutyRole(role) if role else None,
                                                 row.get("session_id"), row.get("module_code")))
        if not isinstance(data["rank_order"], list):
            _fail("eligibility config rank_order must be a list.")
        return cls(tuple(data["rank_order"]), tuple(rules), data["minimum_gap_minutes"], data["source"], tuple(restrictions))
