import { cache, type ReactNode } from "react";

import { Badge, Card, EmptyState } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import type { ApiError } from "@/lib/service-api";
import type { StaffTeachingVersion } from "@/lib/staff-detail";

import { allSessions, getWorkloadReport, listTimetables, SESSION_TYPES, type WorkloadStaff } from "@/lib/teaching";
import { HISTORICAL_PREVIEW_LABEL, modulesTaught, previewStaff, type StaffModule } from "@/lib/teaching-preview";

// Teaching sections of the staff detail page. Live data comes only from the staff member's OWN institution
// (Teaching Service, authorized per request, keyed by staff number). The Historical Experimental Preview is a
// separate, labelled block from a test institution's saved timetables. Nothing missing is filled in.

const NOT_AVAILABLE = "Not available";

export type Live = { status: "ok"; versions: StaffTeachingVersion[] } | { status: "empty" } | { status: "error"; error: ApiError };

/** One read of the institution's validated timetables per request, shared by the modules and workload cards. */
export const getStaffTeaching = cache(async (institutionId: string, staffNumber: string): Promise<Live> => {
  const listing = await listTimetables(institutionId);
  if (!listing.ok) return { status: "error", error: listing.error };
  const versions = listing.data.items.filter((item) => item.validation_status === "PASSED" && item.scopes.length > 0);
  if (versions.length === 0) return { status: "empty" };
  const rows: StaffTeachingVersion[] = [];
  for (const version of versions) {
    const [report, sessions] = await Promise.all([
      getWorkloadReport(institutionId, version.version_id),
      allSessions(institutionId, version.version_id, { lecturer: staffNumber }),
    ]);
    if (!report.ok) return { status: "error", error: report.error };
    if (!sessions.ok) return { status: "error", error: sessions.error };
    const semesters = [...new Set(version.scopes.map((scope) => scope.semester))].sort().join(", ");
    const streams = [...new Set(version.scopes.map((scope) => scope.stream))].filter(Boolean).map((stream) => stream === "WEEKDAY" ? "Weekday" : "Weekend");
    const years = [...new Set(version.scopes.map((scope) => scope.academic_year))].filter(Number.isInteger).sort((a, b) => a - b);
    rows.push({
      versionId: version.version_id,
      summary: version,
      sessions: sessions.data,
      label: [`Semester ${semesters}`, years.length ? `Year ${years.join(", ")}` : "", ...streams, `version ${version.version_number}`, version.lifecycle_status.toLowerCase().replaceAll("_", " ")].filter(Boolean).join(" · "),
      workload: report.data.staff.find((row) => row.staff_id === staffNumber) ?? null,
      modules: modulesTaught(sessions.data, staffNumber),
    });
  }
  return { status: "ok", versions: rows };
});

const hoursText = (value: number | null | undefined) =>
  value === null || value === undefined ? NOT_AVAILABLE : `${Number.isInteger(value) ? value : value.toFixed(1)} h`;

export function liveReason(live: Exclude<Live, { status: "ok" }>, institution: string) {
  return live.status === "empty"
    ? `${institution} has no validated teaching timetable yet.`
    : `The teaching timetable service didn't return this (${live.error.message})`;
}

function Section({ title, badge, children }: { title: string; badge?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-2 px-5 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[13px] font-semibold uppercase tracking-wider text-muted">{title}</h3>
        {badge}
      </div>
      {children}
    </section>
  );
}

function Missing({ children }: { children: ReactNode }) {
  return (
    <p className="flex gap-2 text-sm text-muted">
      <Icon name="info" size={18} className="mt-px shrink-0" />
      <span>
        <span className="font-semibold text-ink">{NOT_AVAILABLE}.</span> {children}
      </span>
    </p>
  );
}

const PreviewBadge = () => <Badge tone="danger">{HISTORICAL_PREVIEW_LABEL}</Badge>;

function PreviewNote({ code }: { code: string }) {
  return (
    <p className="text-[13px] leading-5 text-muted">
      From the saved timetables of test institution {code}, matched to this person by the same staff number and full name.
      Not this institution&apos;s data and not published.
    </p>
  );
}

function ModuleTable({ modules }: { modules: StaffModule[] }) {
  if (modules.length === 0) return <p className="text-sm text-muted">No sessions assigned in this version.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[28rem] text-left text-sm">
        <thead className="text-xs font-semibold uppercase tracking-wider text-muted">
          <tr>
            <th scope="col" className="py-2 pr-3">Module</th>
            <th scope="col" className="py-2 pr-3">Responsibilities</th>
            <th scope="col" className="py-2 text-right">Weekly</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {modules.map((row) => (
            <tr key={row.module_code} className="align-top">
              <td className="py-2 pr-3">
                <span className="font-mono text-xs font-semibold text-ink">{row.module_code}</span>
                <p className="text-[13px] text-ink">{row.module_name}</p>
              </td>
              <td className="py-2 pr-3 text-[13px] text-ink">{row.session_types.map((type) => SESSION_TYPES[type] ?? type).join(", ")}</td>
              <td className="py-2 text-right text-[13px] tabular-nums text-ink">
                {row.sessions} session{row.sessions === 1 ? "" : "s"} · {hoursText(row.minutes / 60)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AssignedModulesCard({ version }: { version: StaffTeachingVersion }) {
  return <Card title="Assigned modules" description="Teaching responsibilities in the selected timetable version."><div className="px-5 py-4"><ModuleTable modules={version.modules} /></div></Card>;
}

export function AvailabilityCard() {
  const rows = [
    ["Available days", "Teaching days a person can work are recorded with lecturer preferences."],
    ["Unavailable periods", "Not Available periods are recorded with lecturer preferences."],
    ["Lecturer preferences", "Preferred and Not Preferred day parts are recorded with lecturer preferences."],
  ];
  return (
    <Card title="Availability & preferences" description="Recorded availability and teaching preferences.">
      <dl className="grid gap-4 px-5 py-5 sm:grid-cols-3">
        {rows.map(([label, detail]) => (
          <div key={label} className="min-w-0">
            <dt className="text-[13px] text-muted">{label}</dt>
            <dd className="font-semibold text-ink">{NOT_AVAILABLE}</dd>
            <dd className="mt-1 text-[13px] leading-5 text-muted">{detail}</dd>
          </div>
        ))}
      </dl>
      <p className="border-t border-line px-5 py-3 text-[13px] leading-5 text-muted">
        Availability and preference details are not available in this view yet.
      </p>
    </Card>
  );
}

function statusBadge(row: WorkloadStaff) {
  if (row.hard_cap_violated) return <Badge tone="danger">Overloaded: over hard cap</Badge>;
  if (row.target_status === "EXCEEDED") return <Badge tone="danger">Above target</Badge>;
  if (row.target_status === "MET") return <Badge tone="accent">Meets target</Badge>;
  if (row.target_status === "BELOW_TARGET") return <Badge tone="primary">Below target</Badge>;
  return <Badge tone="neutral">Overload status not available</Badge>;
}

function WorkloadFacts({ row }: { row: WorkloadStaff }) {
  return (
    <div className="space-y-2">
      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        {[
          ["Assigned teaching", hoursText(row.assigned_teaching_hours)],
          ["Target", hoursText(row.weekly_teaching_target_hours)],
          ["Difference", row.difference_from_target_hours === null ? NOT_AVAILABLE : `${row.difference_from_target_hours > 0 ? "+" : ""}${hoursText(row.difference_from_target_hours)}`],
          ["Teaching days", `${row.teaching_day_count}${row.weekly_teaching_day_limit ? ` of ${row.weekly_teaching_day_limit}` : ""}`],
        ].map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-[13px] text-muted">{label}</dt>
            <dd className="font-semibold text-ink">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-wrap gap-1.5">
        {statusBadge(row)}
        {row.teaching_day_limit_violated && <Badge tone="danger">Over teaching-day limit</Badge>}
      </div>
    </div>
  );
}

export function TeachingWorkloadCard({ version }: { version: StaffTeachingVersion }) {
  return <Card title="Teaching workload" description="Weekly totals from the selected version’s workload report."><div className="p-5">{version.workload ? <WorkloadFacts row={version.workload} /> : <Missing>This person is not in this version&apos;s workload report.</Missing>}</div></Card>;
}

export async function HistoricalTeaching({ staffNumber, fullName }: { staffNumber: string; fullName: string }) {
  const preview = await previewStaff(staffNumber, fullName);
  return <Card title="Historical experimental previews" description="Saved test runs, separate from this institution’s current teaching records.">
    {!preview.length ? <EmptyState icon="history" title="No matching historical previews">No enabled historical preview matches this staff number and full name.</EmptyState> : <div className="divide-y divide-line">{preview.map((version) => <details key={version.versionId}>
      <summary className="cursor-pointer space-y-2 px-5 py-4 text-sm font-semibold text-ink"><span className="mr-3">{version.source.run} · Semester {version.semesters.join(", ")}</span><PreviewBadge /></summary>
      <div className="divide-y divide-line"><Section title="Assigned modules"><ModuleTable modules={version.modules} /></Section><Section title="Teaching workload"><WorkloadFacts row={version.workload} /></Section><div className="px-5 py-4"><PreviewNote code={version.source.institution_code} /></div></div>
    </details>)}</div>}
  </Card>;
}
