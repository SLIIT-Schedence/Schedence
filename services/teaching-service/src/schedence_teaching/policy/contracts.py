"""Version/approval inputs and a deeply immutable effective policy seal.

Approval evidence is supplied by a trusted configuration provider. It is not an
authorization grant, a JWT role assertion or a public policy-management API.
"""
from datetime import date, datetime
from typing import Annotated
from uuid import UUID
import json

from pydantic import ConfigDict, Field, model_validator
from schedence_teaching.schemas.models import InputContract, PositiveInteger, RequiredText
from schedence_teaching.schemas import SessionType
from schedence_teaching.schemas.canonical import canonical_json, content_digest


class PolicyApproval(InputContract):
    approved_by_reference: RequiredText
    authorization_reference: RequiredText
    approval_reference: RequiredText
    approved_at: datetime

    @model_validator(mode='after')
    def aware_approval(self):
        if self.approved_at.tzinfo is None or self.approved_at.utcoffset() is None:
            raise ValueError('Policy approval timestamp must include its timezone.')
        return self


class PolicyRevision(InputContract):
    institution_id: UUID
    policy_reference: RequiredText
    revision: RequiredText
    effective_from: date
    effective_until: date
    approval: PolicyApproval

    @model_validator(mode='after')
    def valid_revision(self):
        if self.institution_id.int == 0 or self.effective_until < self.effective_from:
            raise ValueError('Policy requires an institution and valid effective dates.')
        return self


class StaffPolicyOverride(InputContract):
    staff_id: RequiredText
    effective_from: date
    effective_until: date
    approval: PolicyApproval
    weekly_teaching_target_minutes: PositiveInteger | None = None
    daily_teaching_max_minutes: Annotated[int, Field(strict=True, ge=0)] | None = None
    weekly_teaching_max_minutes: Annotated[int, Field(strict=True, ge=0)] | None = None
    maximum_teaching_days: Annotated[int, Field(strict=True, ge=1, le=7)] | None = None
    session_types: set[SessionType] | None = None

    @model_validator(mode='after')
    def explicit_override(self):
        if self.effective_until < self.effective_from:
            raise ValueError('Staff override dates are reversed.')
        if self.session_types is not None and not self.session_types:
            raise ValueError('An explicit session permission must contain at least one type.')
        if not any(getattr(self, key) is not None for key in (
                'weekly_teaching_target_minutes', 'daily_teaching_max_minutes',
                'weekly_teaching_max_minutes', 'maximum_teaching_days', 'session_types')):
            raise ValueError('Staff override must specify an effective change.')
        return self


class ResolvedInstitutionPolicy(InputContract):
    """Only immutable scalars: no nested mutable dict, list, set or model escapes.

    JSON properties are canonical strings. Consumers get detached values through
    json.loads; changing those values cannot modify the seal. The digest covers
    revision, approval, defaults, rules, override provenance and effective inputs.
    """
    model_config = ConfigDict(extra='forbid', frozen=True)
    format_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    institution_id: UUID
    policy_reference: RequiredText
    revision: RequiredText
    effective_from: date
    effective_until: date
    approval_json: str
    defaults_json: str
    rank_appointment_rules_json: str
    staff_overrides_json: str
    effective_json: str
    digest: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]

    @model_validator(mode='after')
    def consistent_digest(self):
        if self.institution_id.int == 0 or self.effective_until < self.effective_from:
            raise ValueError('Resolved policy scope is invalid.')
        for name in ('approval_json', 'defaults_json', 'rank_appointment_rules_json',
                     'staff_overrides_json', 'effective_json'):
            value = json.loads(getattr(self, name))
            if canonical_json(value) != getattr(self, name):
                raise ValueError('Resolved policy JSON must be canonical.')
        if content_digest(self.model_dump(exclude={'digest'})) != self.digest:
            raise ValueError('Resolved institution policy digest mismatch.')
        return self

    def verify(self):
        # Also detects low-level object.__setattr__/model_copy validation bypasses.
        return type(self).model_validate(self.model_dump(round_trip=True))
