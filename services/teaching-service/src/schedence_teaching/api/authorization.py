"""Identity-owned roles/grants; academic titles and JWT custom claims grant nothing."""

from dataclasses import dataclass, field
from types import MappingProxyType
from uuid import UUID

from .errors import ApiError

READ = "teaching:timetables:read"
READ_SELF = "teaching:timetables:read:self"
READ_SELF_DRAFTS = "teaching:timetables:read:self:drafts"
READ_PUBLISHED = "teaching:timetables:read:published"
# Compatibility with the existing Identity catalogue. No grants are seeded here.
LEGACY_READ_PERMISSIONS = frozenset({"teaching:generate", "teaching:approve"})
FUTURE_MANAGEMENT_PERMISSIONS = frozenset({"teaching:generate", "teaching:timetables:edit",
    "teaching:timetables:regenerate", "teaching:approve", "teaching:timetables:publish"})


@dataclass(frozen=True)
class InstitutionGrant:
    roles: frozenset[str]
    permissions: frozenset[str]


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    cognito_sub: str
    grants: object = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class AcademicBinding:
    """Owner-resolved scope, never populated from JWT/header/query claims.

    A future trusted Academic integration supplies the Identity user link and
    provenance. Student group IDs identify authorized attendance populations.
    """
    user_id: UUID
    institution_id: UUID
    source_reference: str
    staff_id: str | None = None
    group_ids: frozenset[str] = frozenset()
    academic_years: frozenset[int] = frozenset()
    semesters: frozenset[int] = frozenset()
    streams: frozenset[str] = frozenset()
    specializations: frozenset[str] = frozenset()


class MissingAcademicBindings:
    async def resolve(self, principal, institution_id):
        return None


@dataclass(frozen=True)
class Access:
    institution_id: UUID
    mode: str
    binding: AcademicBinding | None = None
    personal_drafts: bool = False


def mode_for(grant):
    permissions = grant.permissions
    if READ in permissions or permissions & LEGACY_READ_PERMISSIONS:
        return "ADMIN"
    if "LECTURER" in grant.roles and READ_SELF in permissions:
        return "LECTURER"
    if "STUDENT" in grant.roles and READ_PUBLISHED in permissions:
        return "STUDENT"
    return None


async def authorize(principal, institution_id, bindings):
    eligible = {key: mode_for(grant) for key, grant in principal.grants.items() if mode_for(grant)}
    if institution_id is not None and institution_id not in eligible:
        raise ApiError(403, "INSTITUTION_SCOPE", "You do not have Teaching access to this institution.")
    if institution_id is None:
        if not eligible:
            raise ApiError(403, "FORBIDDEN", "Teaching timetable permission is required.")
        if len(eligible) != 1:
            raise ApiError(422, "INSTITUTION_REQUIRED", "Select an authorized institution.")
        institution_id = next(iter(eligible))
    mode = eligible[institution_id]
    if mode == "ADMIN":
        return Access(institution_id, mode)
    binding = await bindings.resolve(principal, institution_id)
    if (binding is None or binding.user_id != principal.user_id or binding.institution_id != institution_id
            or not binding.source_reference or (mode == "LECTURER" and not binding.staff_id)
            or (mode == "STUDENT" and not binding.group_ids)):
        raise ApiError(403, "ACADEMIC_SCOPE_UNAVAILABLE", "A trusted academic account mapping is required.")
    if mode == "STUDENT" and (not binding.academic_years or not binding.semesters or not binding.streams
            or not binding.specializations or not binding.academic_years <= {1, 2, 3, 4}
            or not binding.semesters <= {1, 2} or not binding.streams <= {"WEEKDAY", "WEEKEND"}):
        raise ApiError(403, "ACADEMIC_SCOPE_UNAVAILABLE", "A complete trusted student academic scope is required.")
    return Access(institution_id, mode, binding,
        mode == "LECTURER" and READ_SELF_DRAFTS in principal.grants[institution_id].permissions)
