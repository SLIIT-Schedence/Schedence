import type { ReactNode } from "react";
import { Badge, initials } from "@/components/console/ui";
import type { AppointmentStatus, CombinationRule, PositionRecord, StaffRecord, UnitType } from "@/lib/academic";

// Wording and small pieces shared by the Academic Admin pages. Server-safe: no hooks, no client state.

/** The service the Academic Admin pages read from, as LoadError names it. */
export const ACADEMIC_SERVICE = "academic data";

const hoursFormat = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 2 });
const calendarDate = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "UTC" });

/** "6 h" or "7.5 h". */
export const hours = (value: number) => `${hoursFormat.format(value)} h`;

/** A calendar date from the service (YYYY-MM-DD), read as that day wherever the server runs. */
export const formatDay = (day: string) => calendarDate.format(new Date(`${day}T00:00:00Z`));

/** "1 Nov 2026 – 31 Oct 2027", or "From 1 Nov 2026" when open-ended. */
export function period(from: string, until: string | null): string {
  return until ? `${formatDay(from)} – ${formatDay(until)}` : `From ${formatDay(from)}`;
}

export const UNIT_TYPES: Record<UnitType, string> = {
  INSTITUTION: "Institution",
  FACULTY: "Faculty",
  DEPARTMENT: "Department",
  PROGRAM: "Program",
  OTHER: "Other unit",
};

/** How a position's hours combine with the holder's other appointments, in words. */
export function combinationRule(rule: CombinationRule | string | null, cap?: number | null): string {
  switch (rule) {
    case "ADDITIVE":
      return "Added to other appointments";
    case "CAPPED":
      return cap == null ? "Added, up to a cap" : `Added, capped at ${hours(cap)}`;
    case "REPLACEMENT":
      return "Largest appointment applies";
    case "SINGLE_APPOINTMENT":
      return "Single appointment";
    case "NO_APPOINTMENTS":
      return "No appointments";
    default:
      return "Not specified";
  }
}

export function AppointmentBadge({ status }: { status: AppointmentStatus }) {
  const [tone, label] = ({ ACTIVE: ["accent", "Active"], SCHEDULED: ["primary", "Scheduled"], ENDED: ["neutral", "Ended"] } as const)[
    status
  ];
  return <Badge tone={tone}>{label}</Badge>;
}

export function EmploymentBadge({ status }: { status: StaffRecord["employmentStatus"] }) {
  return status === "ACTIVE" ? <Badge tone="accent">Active</Badge> : <Badge tone="neutral">Inactive</Badge>;
}

/** A position's state; an active position without hours in force is flagged, since timetables can't count it. */
export function PositionStatus({ position }: { position: PositionRecord }) {
  if (!position.active) return <Badge tone="neutral">Disabled</Badge>;
  if (position.hoursPerWeek === null) return <Badge tone="danger">No hours in force</Badge>;
  return <Badge tone="accent">Active</Badge>;
}

export function StaffAvatar({ staff, size = "md" }: { staff: StaffRecord; size?: "md" | "lg" }) {
  return (
    <span
      aria-hidden="true"
      className={`grid shrink-0 place-items-center rounded-full font-semibold ${
        size === "lg" ? "size-14 text-base" : "size-10 text-[13px]"
      } ${staff.employmentStatus === "ACTIVE" ? "bg-primary-soft text-primary-strong" : "bg-secondary-soft text-secondary-ink"}`}
    >
      {initials(staff.fullName)}
    </span>
  );
}

/** A small mono tag for codes such as staff numbers and position codes. */
export function CodeTag({ children }: { children: ReactNode }) {
  return (
    <span className="inline-block rounded-md bg-secondary-soft px-2 py-0.5 font-mono text-xs font-semibold text-secondary-ink">
      {children}
    </span>
  );
}

/** One labelled figure in a detail page's fact row. */
export function Fact({ label, value, children }: { label: string; value: ReactNode; children?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-2xl border border-line bg-surface px-4 py-4 sm:px-5">
      <dt className="text-[13px] font-semibold text-muted">{label}</dt>
      <dd className="mt-1 text-base font-semibold leading-snug text-ink sm:text-lg">{value}</dd>
      {children && <dd className="mt-0.5 text-[13px] text-muted">{children}</dd>}
    </div>
  );
}
