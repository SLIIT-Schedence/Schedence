import Form from "next/form";
import Link from "next/link";
import type { ReactNode } from "react";
import { Badge, EmptyState, formatDate, ToneIcon } from "@/components/console/ui";
import { Icon, type IconName } from "@/components/icon";
import type { DutyApiError } from "@/lib/duty-allocation";
import {
  CHANGE_TONES, ROSTER_TONES, titleCase, type ChangeStatus, type DutyPeriod, type Meta, type RosterStatus, type Violation,
} from "@/lib/duty-allocation-types";
import { ExaminationReadError } from "../../_components/read-error";

// Building blocks for the duty-allocation pages. Server-safe, built from the shared console components.

export const cell = "px-5 py-4 text-sm text-muted";
export const headCell = "px-5 py-3 font-semibold";
export const selectClass =
  "h-10 w-full appearance-none rounded-xl border border-line bg-surface pl-3 pr-9 text-sm text-ink transition-colors hover:border-secondary/70 focus:border-primary";
export const primaryButton =
  "inline-flex min-h-10 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong";
export const secondaryButton =
  "inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:bg-primary-soft";
export const number = new Intl.NumberFormat("en-GB");

export const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value);
export type SearchParams = Promise<Record<string, string | string[] | undefined>>;

/** Read failures, with the cases that are specific to this component explained. */
export function DutyReadError({ error, what }: { error: DutyApiError; what: string }) {
  // 403: say what the service answered and what decides access, so the cause can be found without logs.
  if (error.status === 403) {
    return (
      <div role="status" className="flex flex-col items-center px-5 py-8 text-center">
        <ToneIcon icon="shield_person" tone="danger" />
        <p className="mt-3 text-sm font-semibold text-ink">No access to {what}</p>
        <p className="mt-1 max-w-lg text-sm leading-5 text-muted">
          The Examination Service answered: &ldquo;{error.message}&rdquo;
        </p>
        <p className="mt-3 max-w-lg rounded-xl bg-secondary-soft p-3 text-left text-xs leading-5 text-secondary-ink">
          Access needs the Examination Admin role with the examinations:read permission in the institution the Examination
          Service is configured for (EXAMINATION_INSTITUTION_ID). If the Examination overview also fails, the account&apos;s role is
          in another institution. Ask an Institution Admin of that institution to give you the role, then sign out and in again.
        </p>
      </div>
    );
  }
  // 503: the data source or saved-roster storage is turned off or unavailable; the service says which.
  if (error.status === 503) {
    return (
      <div role="status" className="flex flex-col items-center px-5 py-8 text-center">
        <ToneIcon icon="info" tone="neutral" />
        <p className="mt-3 text-sm font-semibold text-ink">Couldn&apos;t load {what}</p>
        <p className="mt-1 max-w-md text-sm leading-5 text-muted">{error.message}</p>
      </div>
    );
  }
  return <ExaminationReadError error={{ status: error.status, code: error.code, message: error.message }} what={what} />;
}

export type NoticeTone = "info" | "warn" | "danger" | "success";
const NOTICE: Record<NoticeTone, { box: string; icon: IconName }> = {
  info: { box: "bg-secondary-soft text-secondary-ink", icon: "info" },
  warn: { box: "bg-primary-soft text-primary-strong", icon: "info" },
  danger: { box: "bg-danger-soft text-danger", icon: "error" },
  success: { box: "bg-accent text-accent-ink", icon: "check_circle" },
};

export function Notice({ tone = "info", title, children }: { tone?: NoticeTone; title?: string; children?: ReactNode }) {
  return (
    <div role={tone === "danger" ? "alert" : undefined} className={`flex gap-2.5 rounded-xl px-3.5 py-3 text-sm leading-5 ${NOTICE[tone].box}`}>
      <Icon name={NOTICE[tone].icon} size={18} filled className="mt-px shrink-0" />
      <div className="min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className={title ? "mt-1" : ""}>{children}</div>}
      </div>
    </div>
  );
}

export function ViolationList({ violations, limit = 8 }: { violations: Violation[]; limit?: number }) {
  return (
    <ul className="list-disc space-y-1 pl-5">
      {violations.slice(0, limit).map((v, index) => <li key={index}><span className="font-mono text-xs">{v.constraint}</span> {v.message}</li>)}
      {violations.length > limit && <li>…and {violations.length - limit} more</li>}
    </ul>
  );
}

/** Shown whenever the data is synthetic or the result is a temporary preview, so it is never mistaken for a decision. */
export function DataNotice({ meta }: { meta: Pick<Meta, "synthetic" | "notice" | "source_description"> }) {
  return (
    <Notice tone={meta.synthetic ? "warn" : "info"} title={meta.synthetic ? "Synthetic demo data" : "Live examination data"}>
      {meta.notice} <span className="opacity-80">Source: {meta.source_description}.</span>
    </Notice>
  );
}

export function RosterStatusBadge({ status }: { status: RosterStatus }) {
  return <Badge tone={ROSTER_TONES[status] ?? "neutral"}>{titleCase(status)}</Badge>;
}

export function ChangeStatusBadge({ status }: { status: ChangeStatus }) {
  return <Badge tone={CHANGE_TONES[status] ?? "neutral"}>{titleCase(status)}</Badge>;
}

export function Field({ label, children, select = true }: { label: string; children: ReactNode; select?: boolean }) {
  return (
    <label className="block text-xs font-semibold text-muted">
      {label}
      <span className="relative mt-1 block">
        {children}
        {select && <Icon name="chevron_right" size={18} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rotate-90 text-secondary" />}
      </span>
    </label>
  );
}

export const periodLabel = (p: DutyPeriod) =>
  `${p.period_name ?? p.period_code}${p.semester ? ` · Semester ${p.semester}` : ""} (${formatDate(p.start_date)} – ${formatDate(p.end_date)})`;

/** A GET form that switches the page to another exam period. */
export function PeriodBar({ action, periods, period, children }: { action: string; periods: DutyPeriod[]; period?: string; children?: ReactNode }) {
  return (
    <Form action={action} className="grid gap-3 border-b border-line p-4 sm:px-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
      <Field label="Exam period">
        <select name="period" defaultValue={period} className={selectClass}>
          {periods.map((p) => <option key={p.period_code} value={p.period_code}>{periodLabel(p)}</option>)}
        </select>
      </Field>
      <div className="flex flex-wrap gap-2">
        <button type="submit" className={primaryButton}>Show</button>
        {children}
      </div>
    </Form>
  );
}

export function NoPeriods() {
  return <EmptyState icon="calendar_month" title="No exam periods">The duty-allocation data source has no exam periods yet.</EmptyState>;
}

export function TextLink({ href, children }: { href: string; children: ReactNode }) {
  return <Link href={href} className="font-semibold text-primary-strong underline-offset-2 hover:underline">{children}</Link>;
}
