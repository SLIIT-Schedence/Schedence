"""Frozen distribution definitions. No answers or submission contracts exist yet."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .contracts import Contract, FormTemplate, Revision


class Audience(Contract):
    kind: Literal["SELECTED", "DEPARTMENT", "ALL_ELIGIBLE"]
    staff_ids: tuple[UUID, ...] = Field(default=(), max_length=10000)
    department_id: UUID | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.kind == "SELECTED":
            if not self.staff_ids or len(set(self.staff_ids)) != len(self.staff_ids) or self.department_id:
                raise ValueError("Select distinct staff IDs only.")
        elif self.staff_ids or (self.kind == "DEPARTMENT") != (self.department_id is not None):
            raise ValueError("Select a department only for a department audience.")
        if any(identifier.int == 0 for identifier in (*self.staff_ids, *((self.department_id,) if self.department_id else ()))):
            raise ValueError("Audience identifiers must be nonzero.")
        return self


class SendForm(Contract):
    institution_id: UUID
    expected_revision: Revision
    channel: Literal["DIRECT", "SHARE_LINK"]
    audience: Audience
    deadline: AwareDatetime


class DepartmentReference(Contract):
    id: UUID
    name: str


class EligibleLecturer(Contract):
    staff_id: UUID
    staff_number: str
    full_name: str
    department_ids: tuple[UUID, ...] = ()
    departments: tuple[DepartmentReference, ...] = ()
    identity_user_id: UUID | None = None
    link_status: Literal["READY", "NO_LINK", "AMBIGUOUS_LINK"]

    @model_validator(mode="after")
    def valid_link(self) -> Self:
        if self.staff_id.int == 0 or (self.link_status == "NO_LINK") != (self.identity_user_id is None):
            raise ValueError("Recipient account link is inconsistent.")
        if self.link_status == "READY" and self.identity_user_id.int == 0:
            raise ValueError("Ready recipients require a valid account link.")
        return self


class Distribution(Contract):
    id: UUID
    institution_id: UUID
    schema_version: Literal[1] = 1
    channel: Literal["DIRECT", "SHARE_LINK"]
    audience: Audience
    deadline: AwareDatetime
    created_at: AwareDatetime
    created_by: UUID
    frozen_template: FormTemplate
    template_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    recipient_count: int = Field(ge=1)
    unlinked_count: int = Field(ge=0)
    ambiguous_count: int = Field(ge=0)


class DistributionOut(Contract):
    distribution: Distribution
    status: Literal["OPEN", "CLOSED"]
    share_url: str | None
    submissions_enabled: Literal[False] = False


class Recipient(EligibleLecturer):
    first_opened_at: datetime | None = None


class RecipientPage(Contract):
    items: tuple[Recipient, ...]
    total: int
    page: int
    page_size: int


class DistributionPage(Contract):
    items: tuple[DistributionOut, ...]
    total: int
    page: int
    page_size: int


class LecturerForm(Contract):
    distribution_id: UUID
    institution_id: UUID
    deadline: AwareDatetime
    frozen_template: FormTemplate
    first_opened_at: datetime | None
    submissions_enabled: Literal[False] = False


class LecturerAssignment(Contract):
    distribution_id: UUID
    title: str
    deadline: AwareDatetime
    status: Literal["OPEN", "CLOSED"]
    first_opened_at: datetime | None
    submissions_enabled: Literal[False] = False


class LecturerAssignments(Contract):
    items: tuple[LecturerAssignment, ...]
    total: int
    page: int
    page_size: int
