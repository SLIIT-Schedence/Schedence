"""Built-in system roles and permissions: the single source of truth for seeding.

Roles here are *application access roles*. University appointments such as
Head of Department, Dean or Program Coordinator are deliberately absent: they
live in the Academic Data Service and never grant login permissions.

Permissions for future scheduling workflows are declared alongside the
permissions used by existing services, so roles can be prepared in advance.
"""

from __future__ import annotations

from dataclasses import dataclass

SUPER_ADMIN = "SUPER_ADMIN"
ACADEMIC_ADMIN = "ACADEMIC_ADMIN"
EXAMINATION_ADMIN = "EXAMINATION_ADMIN"
VENUE_MANAGER = "VENUE_MANAGER"
LECTURER = "LECTURER"
STUDENT = "STUDENT"

#: Roles that only an existing SUPER_ADMIN (in the same institution) may grant or revoke,
#: and whose holders only a SUPER_ADMIN may modify.
PRIVILEGED_ROLES = frozenset({SUPER_ADMIN})


@dataclass(frozen=True)
class RoleDefinition:
    code: str
    name: str
    description: str
    permissions: frozenset[str]


PERMISSIONS: dict[str, str] = {
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

_IDENTITY_ALL = frozenset(p for p in PERMISSIONS if p.startswith("identity:"))
_ACADEMIC_ALL = frozenset(p for p in PERMISSIONS if p.startswith("academic:"))

ROLES: tuple[RoleDefinition, ...] = (
    RoleDefinition(
        SUPER_ADMIN,
        "Administrative Admin",
        "Super Admin: manages accounts, system roles, and administrative position definitions.",
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


def _validate() -> None:
    for role in ROLES:
        unknown = role.permissions - PERMISSIONS.keys()
        if unknown:
            raise RuntimeError(f"Role {role.code} references undefined permissions: {sorted(unknown)}")


_validate()
