import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import { Badge, Card, EmptyState, first, Kpi, LoadError, PageHeader, query, TableSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { allStaff, getAcademicContext, getWorkload } from "@/lib/academic";
import { chooseVersion, getWorkloadReport, rankLabel, TEACHING_SERVICE, type WorkloadStaff } from "@/lib/teaching";
import { ACADEMIC_SERVICE } from "../_components/academic-ui";
import { DraftNotice, Explain, hoursText, LABEL, minutesAsHours, percent, SELECT, SUBMIT, TeachingUnavailable, VersionBadges, VersionFields } from "../_teaching/teaching-ui";

export const metadata: Metadata = { title: "Staff workload" };

type SearchParams = PageProps<"/academic/workload">["searchParams"];

const KEYS = ["semester", "version", "kind", "status", "staff"] as const;
const STATUS: Record<string, string> = { EXCEEDED: "Above target", MET: "Meets target", BELOW_TARGET: "Below target", NONE: "No target", ZERO: "No sessions" };

export default function StaffWorkloadPage({ searchParams }: PageProps<"/academic/workload">) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Staff workload"
        description="Teaching hours, sessions and teaching days per staff member in a validated timetable version, against each person's own approved target and limits."
      />
      <Suspense fallback={<Card><TableSkeleton rows={8} /></Card>}>
        <Workload searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Workload({ searchParams }: { searchParams: SearchParams }) {
  const context = await getAcademicContext();
  if (!context) redirect("/");
  const raw = await searchParams;
  const params = Object.fromEntries(KEYS.map((key) => [key, first(raw[key])?.slice(0, 300)])) as Partial<Record<(typeof KEYS)[number], string>>;
  const institution = context.primary;

  const chosen = await chooseVersion(institution.id, params);
  if (chosen.status !== "ok") {
    return <TeachingUnavailable status={chosen.status} error={chosen.status === "error" ? chosen.error : undefined} institution={institution.name} retryHref="/academic/workload" />;
  }
  const { choice } = chosen;
  const report = await getWorkloadReport(institution.id, choice.version.version_id);
  if (!report.ok) {
    return (
      <Card>
        <LoadError error={report.error} what="the workload report" retryHref="/academic/workload" service={TEACHING_SERVICE} />
      </Card>
    );
  }
  const data = report.data;
  const kind = params.kind === "ACADEMIC" || params.kind === "INSTRUCTOR" ? params.kind : undefined;
  const status = params.status && params.status in STATUS ? params.status : undefined;
  const statusOf = (row: WorkloadStaff) => (row.assigned_delivery_count === 0 ? "ZERO" : (row.target_status ?? "NONE"));
  const rows = data.staff
    .filter((row) => (!kind || row.staff_type === kind) && (!status || statusOf(row) === status || (status === "NONE" && row.target_status === null)))
    .toSorted((a, b) => b.assigned_teaching_hours - a.assigned_teaching_hours || a.staff_name.localeCompare(b.staff_name));
  const selected = data.staff.find((row) => row.staff_id === params.staff);
  const above = data.staff.filter((row) => row.target_status === "EXCEEDED").length;
  const href = (changes: Record<string, string | undefined>) =>
    `/academic/workload${query({ semester: choice.semester, version: choice.version.version_id, kind, status, ...changes })}`;

  return (
    <>
      <DraftNotice version={choice.version} />
      <Card>
        <Form action="/academic/workload" className="grid gap-3 p-4 sm:grid-cols-2 sm:px-5 lg:grid-cols-5">
          <VersionFields choice={choice} />
          <label className={LABEL}>
            Staff type
            <select name="kind" defaultValue={kind ?? ""} className={`mt-1 ${SELECT}`}>
              <option value="">All staff</option>
              <option value="ACADEMIC">Academic staff</option>
              <option value="INSTRUCTOR">Instructors</option>
            </select>
          </label>
          <label className={LABEL}>
            Target status
            <select name="status" defaultValue={status ?? ""} className={`mt-1 ${SELECT}`}>
              <option value="">Any</option>
              {Object.entries(STATUS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <div className="flex items-end">
            <button type="submit" className={SUBMIT}>
              <Icon name="filter_alt_off" size={18} />
              Apply filters
            </button>
          </div>
        </Form>
        <div className="border-t border-line px-5 py-3">
          <VersionBadges version={choice.version} />
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <Kpi icon="badge" tone="primary" label="Staff in this version" value={data.totals.staff_count}
          hint={`${data.totals.academic_staff_count} academic · ${data.totals.instructor_count} instructors`} />
        <Kpi icon="schedule" tone="accent" label="Teaching hours assigned" value={hoursText(data.totals.assigned_teaching_hours)}
          hint={`${data.totals.unique_actual_deliveries} sessions`} />
        <Kpi icon="pending_actions" tone="neutral" label="Staff without sessions" value={data.totals.zero_assigned_count}
          hint="In this version's scope only" />
        <Kpi icon={above ? "error" : "check_circle"} tone={above || data.totals.staff_with_hard_cap_violations ? "danger" : "accent"}
          label="Above own target" value={above} hint={`${data.totals.staff_with_hard_cap_violations} hard-cap violations`} />
      </div>

      {selected && <StaffDetail staff={selected} closeHref={href({})} />}

      <Card title="Staff" description={`${rows.length} of ${data.staff.length} staff · hours are teaching contact hours in this version`}>
        {rows.length === 0 ? (
          <EmptyState icon="filter_alt_off" title="Nobody matches">
            No staff member in this version matches these filters.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[52rem] text-left text-sm">
              <thead className="bg-background/70 text-xs font-semibold uppercase tracking-wider text-muted">
                <tr>
                  {["Staff member", "Teaching", "Sessions", "Teaching days", "Longest day / run", "Own target", "Admin allocation"].map((heading) => (
                    <th key={heading} scope="col" className="px-4 py-3">
                      {heading}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((row) => (
                  <tr key={row.staff_id} className="align-top">
                    <td className="px-4 py-3">
                      <Link href={href({ staff: row.staff_id })} className="font-semibold text-ink hover:text-primary-strong hover:underline">
                        {row.staff_name}
                      </Link>
                      <p className="text-[13px] text-muted">{rankLabel(row.academic_rank)}{row.appointment ? ` · ${row.appointment}` : ""}</p>
                    </td>
                    <td className="px-4 py-3 font-semibold text-ink">{hoursText(row.assigned_teaching_hours)}</td>
                    <td className="px-4 py-3 text-ink">{row.assigned_delivery_count}</td>
                    <td className="px-4 py-3 text-ink">
                      {row.teaching_day_count}
                      <span className="text-muted">{row.weekly_teaching_day_limit ? ` of ${row.weekly_teaching_day_limit} allowed` : ""}</span>
                      {row.teaching_day_limit_violated && <span className="ml-2"><Badge tone="danger">Over limit</Badge></span>}
                    </td>
                    <td className="px-4 py-3 text-ink">
                      {minutesAsHours(row.maximum_daily_teaching_minutes)} / {minutesAsHours(row.longest_continuous_teaching_minutes)}
                    </td>
                    <td className="px-4 py-3">
                      {row.weekly_teaching_target_hours === null ? (
                        <Badge tone="neutral">No target</Badge>
                      ) : (
                        <div className="space-y-1">
                          <p className="text-ink">{hoursText(row.weekly_teaching_target_hours)} · {percent(row.target_utilization?.value)}</p>
                          <TargetBadge status={row.target_status} />
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-ink">{hoursText(row.administrative_allocation_hours)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="space-y-1.5 border-t border-line px-5 py-4">
          <Explain>
            <strong>Teaching</strong> counts each actual session once, including combined sessions. <strong>Longest day / run</strong> is the
            most teaching on one date and the longest unbroken run of back-to-back sessions.
          </Explain>
          <Explain>
            <strong>Own target</strong> comes from each person&apos;s approved workload rule (by rank or appointment), so limits differ between
            staff; staff without a recorded rule have no target and are never given one. <strong>Admin allocation</strong> is the
            administrative time in the timetable&apos;s sealed inputs, kept separate from teaching.
          </Explain>
          {data.notes.map((note) => (
            <Explain key={note}>{note}</Explain>
          ))}
        </div>
      </Card>
    </>
  );
}

function TargetBadge({ status }: { status: WorkloadStaff["target_status"] }) {
  if (status === "EXCEEDED") return <Badge tone="danger">Above target</Badge>;
  if (status === "MET") return <Badge tone="accent">Meets target</Badge>;
  if (status === "BELOW_TARGET") return <Badge tone="primary">Below target</Badge>;
  return <Badge tone="neutral">Not verified</Badge>;
}

/** One person: teaching breakdown, sealed limits, and current administrative hours from the Academic Data Service. */
async function StaffDetail({ staff, closeHref }: { staff: WorkloadStaff; closeHref: string }) {
  const register = await allStaff();
  const record = register.ok ? register.data.items.find((row) => row.staffNumber === staff.staff_id) : undefined;
  const admin = record ? await getWorkload(record.id) : undefined;
  const adminHours = admin?.ok && admin.data.aggregate.computed ? admin.data.aggregate.totalHoursPerWeek : null;
  const facts: [string, string][] = [
    ["Lecture", hoursText(staff.lecture_hours)],
    ["Tutorial", hoursText(staff.tutorial_hours)],
    ["Workshop", hoursText(staff.workshop_hours)],
    ["Lab", hoursText(staff.lab_hours)],
    ["In person / online", `${hoursText(staff.physical_hours)} / ${hoursText(staff.online_hours)}`],
    ["Weekday / weekend", `${hoursText(staff.weekday_hours)} / ${hoursText(staff.weekend_hours)}`],
    ["Weekly cap", staff.weekly_teaching_cap_hours === null ? "None set" : hoursText(staff.weekly_teaching_cap_hours)],
    ["Daily cap", staff.daily_teaching_cap_minutes === null ? "None set" : minutesAsHours(staff.daily_teaching_cap_minutes)],
  ];
  return (
    <Card
      title={staff.staff_name}
      description={`${rankLabel(staff.academic_rank)} · rule from ${staff.rule_source ?? "no recorded rule"}`}
      actions={
        <Link href={closeHref} className="inline-flex items-center gap-1 text-sm font-semibold text-primary-strong hover:underline">
          <Icon name="close" size={16} />
          Close
        </Link>
      }
    >
      <div className="grid gap-5 px-5 py-5 lg:grid-cols-2">
        <div>
          <p className="text-[13px] font-semibold text-muted">Teaching in this version</p>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            {facts.map(([label, value]) => (
              <div key={label}>
                <dt className="text-[13px] text-muted">{label}</dt>
                <dd className="font-semibold text-ink">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="space-y-3">
          <p className="text-[13px] font-semibold text-muted">Administrative workload (Academic Data Service, current)</p>
          {!register.ok ? (
            <LoadError error={register.error} what="the staff register" retryHref={closeHref} service={ACADEMIC_SERVICE} />
          ) : !record ? (
            <p className="text-sm text-muted">No staff record with number {staff.staff_id} in your institution&apos;s staff register, so current administrative hours are unavailable.</p>
          ) : !admin?.ok ? (
            <p className="text-sm text-muted">Current administrative hours could not be loaded.</p>
          ) : admin.data.appointments.length === 0 ? (
            <p className="text-sm text-muted">No administrative appointments recorded.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {admin.data.appointments.map((item) => (
                <li key={item.appointmentId} className="flex justify-between gap-3">
                  <span className="text-ink">{item.positionName}{item.unitName ? ` · ${item.unitName}` : ""}</span>
                  <span className="font-semibold text-ink">{hoursText(item.hoursPerWeek)}</span>
                </li>
              ))}
              <li className="text-[13px] text-muted">
                {admin.data.aggregate.computed ? `Total ${hoursText(admin.data.aggregate.totalHoursPerWeek)} a week (${admin.data.aggregate.rule ?? "rule not recorded"})` : `Total not computed: ${admin.data.aggregate.reason ?? "no rule"}`}
              </li>
            </ul>
          )}
          {adminHours !== null && (
            <p className="rounded-xl bg-background px-3 py-2 text-sm">
              <span className="text-muted">Calculated combined workload: </span>
              <span className="font-semibold text-ink">
                {hoursText(staff.assigned_teaching_hours)} teaching + {hoursText(adminHours)} administrative = {hoursText(staff.assigned_teaching_hours + adminHours)} a week
              </span>
            </p>
          )}
        </div>
      </div>
    </Card>
  );
}
