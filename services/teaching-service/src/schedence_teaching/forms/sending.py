"""Recipient-authorized distribution, without mail delivery or answer submission."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from urllib.parse import urlsplit
from uuid import uuid4

from schedence_teaching.api.errors import ApiError

from .sending_contracts import Distribution, DistributionOut, LecturerAssignment, LecturerForm, Recipient
from .service import FormTemplates


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def public_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise ValueError("Forms public origin must be a trusted HTTPS origin or loopback development origin")
    return value.rstrip("/")


class FormSending:
    def __init__(self, store, directory, origin, *, clock=None):
        self.store, self.directory, self.origin = store, directory, public_origin(origin)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def present(self, row):
        return DistributionOut(distribution=row, status="OPEN" if self.clock() < row.deadline else "CLOSED",
            share_url=(f"{self.origin}/forms/{row.id}?institution_id={row.institution_id}"
                       if row.channel == "SHARE_LINK" else None))

    async def get(self, institution, identifier):
        row = await self.store.get(institution, identifier)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "Form distribution not found.")
        return row

    async def lecturers(self, institution, bearer_token=None, *, for_user=None):
        if getattr(self.directory, "requires_bearer_token", False):
            return await self.directory.eligible(institution, bearer_token=bearer_token, for_user=for_user)
        return await self.directory.eligible(institution)

    async def send(self, template_id, body, actor, key, *, bearer_token=None):
        data = body.model_dump(mode="json")
        data["audience"]["staff_ids"].sort()
        data["deadline"] = body.deadline.astimezone(timezone.utc).isoformat()
        request_hash = digest(dict(template_id=str(template_id), **data))
        previous = await self.store.lookup(body.institution_id, actor, key, request_hash)
        if previous is not None:
            return self.present(previous)
        if body.deadline <= self.clock():
            raise ApiError(422, "INVALID_DEADLINE", "The deadline must be in the future and include a timezone.")
        lecturers = await self.lecturers(body.institution_id, bearer_token)
        audience = body.audience
        if audience.kind == "SELECTED":
            selected = set(audience.staff_ids)
            lecturers = tuple(row for row in lecturers if row.staff_id in selected)
            if {row.staff_id for row in lecturers} != selected:
                raise ApiError(422, "INVALID_RECIPIENTS", "Every selected staff member must be an eligible lecturer in this institution.")
        elif audience.kind == "DEPARTMENT":
            lecturers = tuple(row for row in lecturers if audience.department_id in row.department_ids)
        if not lecturers:
            raise ApiError(422, "EMPTY_AUDIENCE", "No eligible lecturers match this audience.")
        if len(lecturers) > 10000:
            raise ApiError(422, "AUDIENCE_TOO_LARGE", "At most 10000 recipients are supported per distribution.")
        # The repository locks and rechecks the draft revision in the same transaction as both inserts.
        template = await self.store.templates.get(body.institution_id, template_id)
        if template is None:
            raise ApiError(404, "NOT_FOUND", "Form template not found.")
        if template.revision != body.expected_revision:
            raise ApiError(409, "REVISION_CONFLICT", "The template changed; reload before sending.")
        if not template.questions:
            raise ApiError(422, "EMPTY_FORM", "Add at least one question before sending.")
        FormTemplates.validate(template.preset_id, template.questions)
        row = Distribution(id=uuid4(), institution_id=body.institution_id, channel=body.channel, audience=audience,
            deadline=body.deadline.astimezone(timezone.utc), created_at=self.clock(), created_by=actor,
            frozen_template=template, template_sha256=digest(template.model_dump(mode="json")),
            recipient_count=len(lecturers), unlinked_count=sum(r.link_status == "NO_LINK" for r in lecturers),
            ambiguous_count=sum(r.link_status == "AMBIGUOUS_LINK" for r in lecturers))
        result = await self.store.create(row, tuple(Recipient(**r.model_dump()) for r in lecturers), key, request_hash)
        return self.present(result)

    @staticmethod
    def lecturer_grant(principal, institution):
        grant = principal.grants.get(institution)
        if grant is None or "LECTURER" not in grant.roles:
            raise ApiError(403, "LECTURER_REQUIRED", "Lecturer access is required in this institution.")

    async def inbox(self, institution, principal, offset, limit, *, bearer_token=None):
        self.lecturer_grant(principal, institution)
        current = [r for r in await self.lecturers(institution, bearer_token, for_user=principal.user_id)
                   if r.identity_user_id == principal.user_id and r.link_status == "READY"]
        if len(current) != 1:
            return (), 0
        rows, total = await self.store.inbox(institution, principal.user_id, current[0].staff_id, offset, limit)
        return tuple(LecturerAssignment(distribution_id=row.id, title=row.frozen_template.title, deadline=row.deadline,
            status="OPEN" if self.clock() < row.deadline else "CLOSED", first_opened_at=opened) for row, opened in rows), total

    async def lecturer_access(self, institution, identifier, principal, *, opened=False, bearer_token=None):
        self.lecturer_grant(principal, institution)
        # A link is a locator only. Require the frozen identity AND its current unique Academic link.
        recipient = await self.store.recipient_for(institution, identifier, principal.user_id)
        if recipient is None or recipient.link_status != "READY":
            raise ApiError(404, "NOT_FOUND", "Form distribution not found.")
        current = [r for r in await self.lecturers(institution, bearer_token, for_user=principal.user_id)
                   if r.identity_user_id == principal.user_id and r.link_status == "READY"]
        if len(current) != 1 or current[0].staff_id != recipient.staff_id:
            raise ApiError(403, "RECIPIENT_LINK_CHANGED", "Your current staff account link does not authorize this form.")
        row = await self.get(institution, identifier)
        if self.clock() >= row.deadline:
            raise ApiError(410, "FORM_CLOSED", "The form deadline has passed.")
        if opened:
            recipient = await self.store.mark_opened(institution, identifier, recipient.staff_id, self.clock())
        return LecturerForm(distribution_id=row.id, institution_id=institution, deadline=row.deadline,
            frozen_template=row.frozen_template, first_opened_at=recipient.first_opened_at)
