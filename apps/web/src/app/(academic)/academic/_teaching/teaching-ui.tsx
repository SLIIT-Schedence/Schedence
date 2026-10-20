import Link from "next/link";
import type { ReactNode } from "react";
import { Badge, Card, EmptyState, LoadError } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import type { ApiError } from "@/lib/service-api";
import { TEACHING_SERVICE, type TimetableSummary, type VersionChoice } from "@/lib/teaching";
import { HISTORICAL_PREVIEW, HISTORICAL_PREVIEW_LABEL, historicalPreviewEnabled, previewOrigin } from "@/lib/teaching-preview";

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
  if (retryHref.includes(`source=${HISTORICAL_PREVIEW}`)) {
    return (
      <Card>
        <EmptyState icon="history" title={`${HISTORICAL_PREVIEW_LABEL} unavailable`} action={<Retry href={retryHref} />}>
          {error?.message ?? "The saved preview could not be read."}
        </EmptyState>
      </Card>
    );
  }
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
        <div className="flex justify-center pb-8">
          <PreviewLink href={`${retryHref.split("?")[0]}?source=${HISTORICAL_PREVIEW}`} />
        </div>
      </Card>
    );
  }
  return (
    <Card>
      <EmptyState icon="calendar_month" title="No validated timetables yet" action={<PreviewLink href={`${retryHref.split("?")[0]}?source=${HISTORICAL_PREVIEW}`} />}>
        {institution} has no teaching timetable version that has passed independent validation, so there are no
        sessions or workloads to show. They appear here once a timetable is generated and validated for this institution.
      </EmptyState>
    </Card>
  );
}

/** Entry to the Historical Experimental Preview, shown only when the development preview is enabled. */
export function PreviewLink({ href }: { href: string }) {
  if (!historicalPreviewEnabled()) return null;
  return (
    <Link
      href={href}
      prefetch={false}
      className="inline-flex h-10 items-center gap-2 rounded-xl border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:bg-background"
    >
      <Icon name="history" size={18} />
      Open the {HISTORICAL_PREVIEW_LABEL}
    </Link>
  );
}

/**
 * Replaces the draft notice in preview mode: names the run's own institution, so the preview can never be read
 * as the signed-in institution's timetable.
 */
export async function PreviewNotice({ institution, backHref, versionId, semester, year }: {
  institution: string;
  backHref: string;
  versionId: string;
  /** The selection on the page; incomplete-scope warnings appear only when they apply to it. */
  semester?: number;
  year?: number;
}) {
  const origin = await previewOrigin(versionId);
  const source = origin?.source;
  const stored = source?.kind === "STORED_HISTORICAL_VERSION";
  const issues = [
    ...(origin?.incomplete ?? []).map((row) => ({
      semester: row.semester,
      year: row.academic_year,
      text: `Year ${row.academic_year} ${row.specialization} (${row.stream.toLowerCase()}) is not scheduled: missing academic mapping${row.student_count ? `, ${row.student_count} students` : ""}.`,
    })),
    ...(origin?.researchPending ? [{ semester: undefined, year: 4, text: "Compulsory Year 4 research has no session mapping and is not scheduled." }] : []),
  ];
  const relevant = issues.filter((row) => (semester === undefined || row.semester === undefined || row.semester === semester) && year !== undefined && row.year === year);
  return (
    <div className="rounded-2xl border border-danger/20 bg-danger-soft px-4 py-2.5 text-sm leading-6 text-danger">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="inline-flex items-center gap-1.5 font-semibold">
          <Icon name="history" size={18} filled />
          {HISTORICAL_PREVIEW_LABEL}
        </span>
        <span className="text-[13px]">
          {source?.institution_code ?? "Test institution"} · not {institution} data · not published
        </span>
        <Link href={backHref} className="ml-auto text-[13px] font-semibold underline underline-offset-2">
          Back to {institution}
        </Link>
      </div>
      {relevant.length > 0 && (
        <ul className="mt-1 space-y-0.5 text-[13px]">
          {relevant.map((row) => (
            <li key={row.text} className="flex gap-1.5">
              <Icon name="info" size={16} className="mt-1" />
              {row.text}
            </li>
          ))}
        </ul>
      )}
      <details className="group mt-0.5 text-[13px]">
        <summary className="inline-flex cursor-pointer list-none items-center gap-1 font-semibold [&::-webkit-details-marker]:hidden">
          <Icon name="chevron_right" size={16} className="transition-transform group-open:rotate-90" />
          About this preview
        </summary>
        <div className="mt-1 space-y-1 pl-5">
          <p>
            {source?.run ?? "A saved run"} (completed {source?.run_completed ?? "earlier"}) of the original test institution,{" "}
            {stored ? "an unpublished validated draft read without changes" : "a local run never saved to a database"}, re-checked by
            independent validation and shown read-only.
          </p>
          <p>{source?.settings ?? "Experimental settings"}; not university-approved; dated on a synthetic reference week.</p>
          {issues.length > relevant.length && (
            <p>All incomplete scopes in this version: {issues.map((row) => row.text).join(" ")}</p>
          )}
        </div>
      </details>
    </div>
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
export function VersionFields({ choice, source }: { choice: VersionChoice; source?: string }) {
  return (
    <>
      {source && <input type="hidden" name="source" value={source} />}
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
