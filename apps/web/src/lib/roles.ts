import type { IconName } from "@/components/icon";

// Institution roles as identity-service seeds them (services/identity-service/app/rbac/catalog.py),
// with the wording and icons the consoles use. Safe to import from client components.

export type RoleInfo = { code: string; name: string; description: string; icon: IconName };

export const INSTITUTION_ROLES: RoleInfo[] = [
  {
    code: "INSTITUTION_ADMIN",
    name: "Institution Admin",
    description: "Manages accounts, roles and settings.",
    icon: "admin_panel_settings",
  },
  {
    code: "ACADEMIC_ADMIN",
    name: "Academic Admin",
    description: "Configures and approves teaching timetables.",
    icon: "menu_book",
  },
  {
    code: "EXAMINATION_ADMIN",
    name: "Examination Admin",
    description: "Configures, generates and approves examination timetables and duties.",
    icon: "fact_check",
  },
  {
    code: "VENUE_MANAGER",
    name: "Venue Manager",
    description: "Manages venues, their availability and bookings.",
    icon: "meeting_room",
  },
  { code: "LECTURER", name: "Lecturer", description: "Uses the lecturer portal.", icon: "school" },
  { code: "STUDENT", name: "Student", description: "Uses the student portal.", icon: "backpack" },
];

/** The roles an Institution Admin can invite people into from the console, for now. */
export const INVITABLE_ROLE_CODES = ["EXAMINATION_ADMIN", "VENUE_MANAGER", "LECTURER"] as const;

export type InvitableRole = (typeof INVITABLE_ROLE_CODES)[number];

export const isInvitableRole = (code: string): code is InvitableRole =>
  (INVITABLE_ROLE_CODES as readonly string[]).includes(code);

export function roleInfo(code: string): RoleInfo {
  return (
    INSTITUTION_ROLES.find((role) => role.code === code) ?? {
      code,
      name: code.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase()),
      description: "",
      icon: "verified_user",
    }
  );
}
