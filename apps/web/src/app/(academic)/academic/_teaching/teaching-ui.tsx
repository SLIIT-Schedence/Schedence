import Link from "next/link";
import type { ReactNode } from "react";
import { Badge, Card, EmptyState, LoadError } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import type { ApiError } from "@/lib/service-api";
import { TEACHING_SERVICE, type TimetableSummary, type VersionChoice } from "@/lib/teaching";

// Shared pieces of the Academic Admin teaching pages (sessions, workload, fairness). Server-safe.

export const SELECT =
  "h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm text-ink transition-colors hover:border-secondary/70 focus:border-primary";
export const LABEL = "block text-[13px] font-semibold text-muted";
export const SUBMIT =
  "inline-flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong";

/** "12 h" or "7.5 h". */
export const hoursText = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${Number.isInteger(value) ? value : value.toFixed(1)} h`;
export const minutesAsHours = (minutes: number | null | undefined) =>
  minutes === null || minutes === undefined ? "—" : hoursText(Math.round((minutes / 60) * 10) / 10);
export const percent = (value: number | null | undefined) => (value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`);

/** What to show when the institution's validated versions can't be listed, or there are none. */
export function TeachingUnavailable({ status, error, institution, retryHref }: { status: "empty" | "error"; error?: ApiError; institution: string; retryHref: string }) {
  if (status === "error" && error?.code === "PREVIEW_LOADING") {
    return (
      <Card>
        <EmptyState icon="schedule" title="Validating the saved timetables" action={<Retry href={retryHref} />}>
          The teaching timetable service re-checks every saved timetable before serving it. This takes a minute or two
          after the service starts.
        </EmptyState>
      </Card>
    );
  }
  if (status === "error" && error) {
    return (
      <Card>
        <LoadError error={error} what="teaching timetables" retryHref={retryHref} service={TEACHING_SERVICE} />
      </Card>
    );
  }
  return (
    <Card>
      <EmptyState icon="calendar_month" title="No validated timetables yet">
        {institution} has no teaching timetable version that has passed independent validation, so there are no
        sessions or workloads to show. They appear here once a timetable is generated and validated for this institution.
      </EmptyState>
    </Card>
  );
}

/** The experimental-draft notice: every version shown here is unpublished. */
export function DraftNotice({ version }: { version: TimetableSummary }) {
  return (
    <p className="flex gap-2 rounded-2xl border border-danger/20 bg-danger-soft px-4 py-3 text-sm leading-6 text-danger">
      <Icon name="info" size={20} filled className="mt-0.5" />
      <span>
        {version.preview_label ? (
          <>
            <strong>{version.preview_label}.</strong> A local development preview generated with provisional scheduling
            settings; not an approved or published university timetable.
          </>
        ) : (
          <>
            <strong>Not published.</strong> A validated draft version; it is not an approved or published timetable.
          </>
        )}
      </span>
    </p>
  );
}

export function VersionBadges({ version }: { version: TimetableSummary }) {
  return (
    <div className="flex flex-wrap gap-2">
      {version.solver_status === "OPTIMAL" ? (
        <Badge tone="accent">Solver: optimal</Badge>
      ) : (
        <Badge tone="primary">Solver: {version.solver_status.toLowerCase()} — not proven optimal</Badge>
      )}
      {version.validation_status === "PASSED" ? <Badge tone="accent">Independent validation passed</Badge> : <Badge tone="danger">Validation failed</Badge>}
      {!version.academic_complete && <Badge tone="neutral">Partial academic coverage</Badge>}
    </div>
  );
}

const yearsOf = (version: TimetableSummary, semester: number) =>
  [...new Set(version.scopes.filter((scope) => scope.semester === semester).map((scope) => scope.academic_year))].sort();

export function versionLabel(version: TimetableSummary, semester: number) {
  const years = yearsOf(version, semester);
  const range = years.length === 1 ? `Year ${years[0]} only` : `Years ${years[0]}–${years[years.length - 1]}`;
  return `${range} · ${version.solver_status.toLowerCase()}`;
}

/** Semester and (when a semester has several) version selects, for a GET filter form. */
export function VersionFields({ choice }: { choice: VersionChoice }) {
  return (
    <>
      <label className={LABEL}>
        Semester
        <select name="semester" defaultValue={choice.semester} className={`mt-1 ${SELECT}`}>
          {choice.semesters.map((semester) => (
            <option key={semester} value={semester}>
              Semester {semester}
            </option>
          ))}
        </select>
      </label>
      {choice.semesterVersions.length > 1 && (
        <label className={LABEL}>
          Timetable version
          <select name="version" defaultValue={choice.version.version_id} className={`mt-1 ${SELECT}`}>
            {choice.semesterVersions.map((version) => (
              <option key={version.version_id} value={version.version_id}>
                {versionLabel(version, choice.semester)}
              </option>
            ))}
          </select>
        </label>
      )}
    </>
  );
}

export function Retry({ href }: { href: string }) {
  return (
    <Link
      href={href}
      prefetch={false}
      className="inline-flex h-10 items-center gap-2 rounded-xl border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:bg-background"
    >
      <Icon name="refresh" size={18} />
      Try again
    </Link>
  );
}

/** A short explanation shown under a metric or table. */
export function Explain({ children }: { children: ReactNode }) {
  return <p className="text-[13px] leading-5 text-muted">{children}</p>;
}
