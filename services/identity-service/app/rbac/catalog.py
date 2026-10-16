"""Built-in system roles and permissions: the single source of truth for seeding.

Roles here are *application access roles*. University appointments such as
Head of Department, Dean or Program Coordinator are deliberately absent: they
live in the Academic Data Service and never grant login permissions.

Roles have one of two scopes:

* ``PLATFORM`` roles (SYSTEM_ADMIN, held by the Schedence product owners) are
  granted outside any institution and carry only ``platform:*`` permissions:
  registering institutions and their Institution Admins. They grant nothing
  inside an institution, so a System Admin cannot see lecturers, students or
  academic data.
* ``INSTITUTION`` roles are always granted within one institution.

Permissions for future scheduling workflows are declared alongside the
permissions used by existing services, so roles can be prepared in advance.
"""

from __future__ import annotations

from dataclasses import dataclass

SYSTEM_ADMIN = "SYSTEM_ADMIN"
INSTITUTION_ADMIN = "INSTITUTION_ADMIN"
ACADEMIC_ADMIN = "ACADEMIC_ADMIN"
EXAMINATION_ADMIN = "EXAMINATION_ADMIN"
VENUE_MANAGER = "VENUE_MANAGER"
LECTURER = "LECTURER"
STUDENT = "STUDENT"

SCOPE_PLATFORM = "PLATFORM"
SCOPE_INSTITUTION = "INSTITUTION"

#: Institution roles that only an INSTITUTION_ADMIN (of the same institution) or a System Admin
#: may grant or revoke, and whose holders only an INSTITUTION_ADMIN of that institution may modify.
PRIVILEGED_ROLES = frozenset({INSTITUTION_ADMIN})


@dataclass(frozen=True)
class RoleDefinition:
    code: str
    name: str
    description: str
    permissions: frozenset[str]
    scope: str = SCOPE_INSTITUTION


PERMISSIONS: dict[str, str] = {
    # Platform (System Admin only; never granted within an institution)
    "platform:institutions:create": "Register new institutions",
    "platform:institutions:read": "View registered institutions",
    "platform:institutions:update": "Rename institutions and change their codes",
    "platform:institutions:disable": "Disable and re-enable institutions",
    "platform:institution_admins:create": "Create Institution Admin accounts and resend their invitations",
    "platform:institution_admins:read": "View the Institution Admins of an institution",
    "platform:institution_admins:revoke": "Revoke the Institution Admin role",
    "platform:institution_admins:disable": "Disable and re-enable Institution Admin accounts",
    "platform:audit:read": "View platform audit logs",
    # Identity Service
    "identity:users:create": "Create user accounts (provisions the Cognito identity)",
    "identity:users:read": "View and search user accounts",
    "identity:users:update": "Update permitted user profile fields",
    "identity:users:disable": "Disable and re-enable user accounts",
    "identity:roles:read": "View system roles",
    "identity:roles:assign": "Assign and revoke system roles",
    "identity:permissions:read": "View permissions",
    "identity:audit:read": "View identity audit logs",
    # Academic Data Service: administrative positions and appointments
    "academic:positions:create": "Create administrative position types",
    "academic:positions:read": "View administrative position types and workload policies",
    "academic:positions:update": "Update position definitions and administrative-hour policies",
    "academic:positions:disable": "Disable or re-enable position types",
    "academic:appointments:assign": "Assign administrative positions to staff",
    "academic:appointments:read": "View staff appointments and their history",
    "academic:appointments:update": "Update permitted appointment details",
    "academic:appointments:end": "End staff appointments",
    "academic:staff:create": "Register academic staff references",
    "academic:staff:read": "View academic staff references",
    "academic:workload:read": "Read administrative workload for scheduling",
    # Teaching Timetable Service (future)
    "teaching:generate": "Generate teaching timetables",
    "teaching:approve": "Approve and publish teaching timetables",
    # Examination Service (generation and approval workflows are future work)
    "examinations:read": "View examination data, timetables and reports; compare and export timetables",
    "examinations:manage": "Configure examination settings and edit draft timetables",
    "examinations:generate": "Generate examination timetables",
    "examinations:approve": "Approve and publish examination timetables",
    # Venue Service (future)
    "venues:read": "View venues and availability",
    "venues:manage": "Manage venues, availability and bookings",
}

PLATFORM_PERMISSION_PREFIX = "platform:"

_PLATFORM_ALL = frozenset(p for p in PERMISSIONS if p.startswith(PLATFORM_PERMISSION_PREFIX))
_IDENTITY_ALL = frozenset(p for p in PERMISSIONS if p.startswith("identity:"))
_ACADEMIC_ALL = frozenset(p for p in PERMISSIONS if p.startswith("academic:"))

ROLES: tuple[RoleDefinition, ...] = (
    RoleDefinition(
        SYSTEM_ADMIN,
        "System Admin",
        "Schedence product owners: register institutions and create their Institution Admins. No access inside an institution.",
        _PLATFORM_ALL,
        scope=SCOPE_PLATFORM,
    ),
    RoleDefinition(
        INSTITUTION_ADMIN,
        "Institution Admin",
        "Manages the institution's accounts, system roles, and administrative position definitions.",
        _IDENTITY_ALL | _ACADEMIC_ALL,
    ),
    RoleDefinition(
        ACADEMIC_ADMIN,
        "Academic Admin",
        "Configures, generates and approves teaching timetables.",
        frozenset({
            "teaching:generate", "teaching:approve", "academic:positions:read", "academic:appointments:read",
            "academic:staff:read", "academic:workload:read", "venues:read",
        }),
    ),
    RoleDefinition(
        EXAMINATION_ADMIN,
        "Examination Admin",
        "Configures, generates and approves examination timetables and duties.",
        frozenset({
            "examinations:read", "examinations:manage", "examinations:generate", "examinations:approve",
            "academic:staff:read", "academic:workload:read", "venues:read",
        }),
    ),
    RoleDefinition(VENUE_MANAGER, "Venue Manager", "Manages venues, availability and bookings.", frozenset({"venues:read", "venues:manage"})),
    RoleDefinition(LECTURER, "Lecturer", "Lecturer portal access.", frozenset()),
    RoleDefinition(STUDENT, "Student", "Student portal access.", frozenset()),
)

ROLE_CODES = frozenset(r.code for r in ROLES)
PLATFORM_ROLE_CODES = frozenset(r.code for r in ROLES if r.scope == SCOPE_PLATFORM)
INSTITUTION_ROLE_CODES = ROLE_CODES - PLATFORM_ROLE_CODES


def role_scope(code: str) -> str:
    """Scope of a role code. Roles outside the catalog (custom roles) are institution roles."""
    return SCOPE_PLATFORM if code in PLATFORM_ROLE_CODES else SCOPE_INSTITUTION


def _validate() -> None:
    for role in ROLES:
        unknown = role.permissions - PERMISSIONS.keys()
        if unknown:
            raise RuntimeError(f"Role {role.code} references undefined permissions: {sorted(unknown)}")
        if role.scope not in (SCOPE_PLATFORM, SCOPE_INSTITUTION):
            raise RuntimeError(f"Role {role.code} has an unknown scope: {role.scope}")
        platform = {p for p in role.permissions if p.startswith(PLATFORM_PERMISSION_PREFIX)}
        if role.scope == SCOPE_PLATFORM and platform != role.permissions:
            raise RuntimeError(f"Platform role {role.code} may only hold platform permissions: {sorted(role.permissions - platform)}")
        if role.scope == SCOPE_INSTITUTION and platform:
            raise RuntimeError(f"Institution role {role.code} may not hold platform permissions: {sorted(platform)}")


_validate()
