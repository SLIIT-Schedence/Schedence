import type { Metadata } from "next";
import Form from "next/form";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import { BarChart } from "@/components/console/charts";
import { Badge, Card, EmptyState, first, LoadError, PageHeader, TableSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { getAcademicContext } from "@/lib/academic";
import { chooseVersion, getWorkloadReport, rankLabel, STAGES, TEACHING_SERVICE, type FairnessComparison, type Ratio, type WorkloadReport } from "@/lib/teaching";
import { DraftNotice, Explain, LABEL, percent, SELECT, SUBMIT, TeachingUnavailable, VersionBadges, VersionFields } from "../_teaching/teaching-ui";

export const metadata: Metadata = { title: "Workload fairness" };

type SearchParams = PageProps<"/academic/fairness">["searchParams"];

const KEYS = ["semester", "version", "group"] as const;
const groupKey = (row: { academic_rank: string | null; appointment: string | null }) => `${row.academic_rank ?? "NONE"}|${row.appointment ?? ""}`;
const groupName = (row: { academic_rank: string | null; appointment: string | null }) =>
  `${rankLabel(row.academic_rank)}${row.appointment ? ` · ${row.appointment}` : ""}`;
const ratioText = (ratio: Ratio | null, digits = 3) => (ratio ? ratio.value.toFixed(digits) : "—");

export default function WorkloadFairnessPage({ searchParams }: PageProps<"/academic/fairness">) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Workload fairness"
        description="How evenly teaching is spread against each person's own target, compared only within comparable staff groups."
      />
      <Suspense fallback={<Card><TableSkeleton rows={8} /></Card>}>
        <Fairness searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Fairness({ searchParams }: { searchParams: SearchParams }) {
  const context = await getAcademicContext();
  if (!context) redirect("/");
  const raw = await searchParams;
  const params = Object.fromEntries(KEYS.map((key) => [key, first(raw[key])?.slice(0, 300)])) as Partial<Record<(typeof KEYS)[number], string>>;
  const institution = context.primary;

  const chosen = await chooseVersion(institution.id, params);
  if (chosen.status !== "ok") {
    return <TeachingUnavailable status={chosen.status} error={chosen.status === "error" ? chosen.error : undefined} institution={institution.name} retryHref="/academic/fairness" />;
  }
  const { choice } = chosen;
  const report = await getWorkloadReport(institution.id, choice.version.version_id);
  if (!report.ok) {
    return (
      <Card>
        <LoadError error={report.error} what="the workload report" retryHref="/academic/fairness" service={TEACHING_SERVICE} />
      </Card>
    );
  }
  const data = report.data;
  const comparisons = data.fairness.descriptive_comparisons;
  const group = comparisons.find((row) => groupKey(row) === params.group) ?? comparisons.toSorted((a, b) => b.staff_count - a.staff_count)[0];
  const members = group ? data.staff.filter((row) => groupKey(row) === groupKey(group)) : [];
  const withTarget = members.filter((row) => row.target_utilization);
  const exceeded = data.staff.filter((row) => row.target_status === "EXCEEDED");
  const capped = data.staff.filter((row) => row.hard_cap_violated);
  const dayLimit = data.staff.filter((row) => row.teaching_day_limit_violated);
  const missing = data.staff.filter((row) => data.fairness.missing_target_staff_ids.includes(row.staff_id));
  const dayCounts = Array.from({ length: 8 }, (_, days) => ({ days, staff: members.filter((row) => row.teaching_day_count === days).length }))
    .filter((row) => row.staff > 0);

  return (
    <>
      <DraftNotice version={choice.version} />
      <Card>
        <Form action="/academic/fairness" className="grid gap-3 p-4 sm:grid-cols-2 sm:px-5 lg:grid-cols-4">
          <VersionFields choice={choice} />
          {comparisons.length > 0 && (
            <label className={LABEL}>
              Comparable staff group
              <select name="group" defaultValue={group ? groupKey(group) : ""} className={`mt-1 ${SELECT}`}>
                {comparisons.map((row) => (
                  <option key={groupKey(row)} value={groupKey(row)}>
                    {groupName(row)} ({row.staff_count})
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="flex items-end">
            <button type="submit" className={SUBMIT}>
              <Icon name="filter_alt_off" size={18} />
              Show
            </button>
          </div>
        </Form>
        <div className="border-t border-line px-5 py-3">
          <VersionBadges version={choice.version} />
        </div>
      </Card>

      <SolverFairness report={data} />

      <Card title="Comparable staff groups" description="Descriptive spread of teaching against own targets, within each rank or appointment group">
        {comparisons.length === 0 ? (
          <EmptyState icon="group" title="No comparisons available">
            The workload report has no staff group with recorded targets to compare in this version.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[44rem] text-left text-sm">
              <thead className="bg-background/70 text-xs font-semibold uppercase tracking-wider text-muted">
                <tr>
                  {["Staff group", "Staff", "Mean of target", "Variance", "Mean abs. deviation", "Largest deviation"].map((heading) => (
                    <th key={heading} scope="col" className="px-4 py-3">
                      {heading}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {comparisons.map((row: FairnessComparison) => (
                  <tr key={groupKey(row)} className={group && groupKey(row) === groupKey(group) ? "bg-primary-soft/40" : undefined}>
                    <td className="px-4 py-3 font-semibold text-ink">{groupName(row)}</td>
                    <td className="px-4 py-3 text-ink">{row.staff_count}</td>
                    <td className="px-4 py-3 text-ink">{percent(row.mean_utilization?.value)}</td>
                    <td className="px-4 py-3 text-ink">{ratioText(row.variance, 4)}</td>
                    <td className="px-4 py-3 text-ink">{percent(row.mean_absolute_deviation?.value)}</td>
                    <td className="px-4 py-3 text-ink">{percent(row.maximum_deviation?.value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="space-y-1.5 border-t border-line px-5 py-4">
          <Explain>
            <strong>Mean of target</strong>: average assigned teaching as a share of each person&apos;s own weekly target (100% = exactly on target).
            <strong> Variance</strong>: spread of those shares around the group mean (0 = everyone equally loaded relative to target).
            <strong> Mean absolute deviation</strong>: average distance from the group mean. <strong>Largest deviation</strong>: the
            furthest anyone is from the group mean.
          </Explain>
          <Explain>
            These figures are descriptive. They compare only staff of the same rank or appointment and do not adjust for who could
            teach which modules, so they do not by themselves establish that the distribution is fair.
          </Explain>
        </div>
      </Card>

      {group && (
        <div className="grid items-start gap-6 xl:grid-cols-2">
          <Card title="Teaching against own target" description={`${groupName(group)} · each person's assigned teaching as a share of their target`}>
            {withTarget.length === 0 ? (
              <EmptyState icon="info" title="No targets in this group">Nobody in this group has a recorded teaching target.</EmptyState>
            ) : (
              <div className="px-5 py-5">
                <BarChart
                  caption={`Teaching as a share of own target, ${groupName(group)}`}
                  unit={{ one: "% of target", other: "% of target" }}
                  shares={false}
                  head={["Staff member", "% of own target"]}
                  bars={withTarget
                    .toSorted((a, b) => (b.target_utilization?.value ?? 0) - (a.target_utilization?.value ?? 0))
                    .map((row) => ({ key: row.staff_id, label: row.staff_name, value: Math.round((row.target_utilization?.value ?? 0) * 100) }))}
                />
              </div>
            )}
          </Card>
          <Card title="Teaching-day distribution" description={`${groupName(group)} · how many staff teach on how many days`}>
            <div className="px-5 py-5">
              <BarChart
                caption={`Staff by number of teaching days, ${groupName(group)}`}
                unit={{ one: "person", other: "people" }}
                head={["Teaching days", "Staff"]}
                bars={dayCounts.map((row) => ({ key: String(row.days), label: row.days === 0 ? "No teaching" : `${row.days} day${row.days === 1 ? "" : "s"}`, value: row.staff }))}
              />
            </div>
          </Card>
        </div>
      )}

      <div className="grid items-start gap-6 xl:grid-cols-2">
        <Card title="Overload indicators" description="Across all staff in this version">
          <ul className="divide-y divide-line text-sm">
            <Indicator label="Above own teaching target" count={exceeded.length} names={exceeded.map((row) => row.staff_name)} />
            <Indicator label="Over an approved hard cap" count={capped.length} names={capped.map((row) => row.staff_name)} />
            <Indicator label="Over the teaching-day limit" count={dayLimit.length} names={dayLimit.map((row) => row.staff_name)} />
          </ul>
        </Card>
        <Card title="Missing information" description="Staff excluded from target-based comparisons">
          {missing.length === 0 ? (
            <EmptyState icon="check_circle" title="Every staff member has a target" />
          ) : (
            <div className="px-5 py-4">
              <Explain>
                These staff have no recorded workload rule, so they have no target. They are left out of every comparison above
                rather than being given an assumed one.
              </Explain>
              <ul className="mt-3 flex flex-wrap gap-2">
                {missing.map((row) => (
                  <li key={row.staff_id}>
                    <Badge tone="neutral">{row.staff_name}{row.source_position ? ` · ${row.source_position}` : ""}</Badge>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}

function Indicator({ label, count, names }: { label: string; count: number; names: string[] }) {
  return (
    <li className="px-5 py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="text-ink">{label}</span>
        <Badge tone={count ? "danger" : "accent"}>{count === 0 ? "None" : `${count} staff`}</Badge>
      </div>
      {count > 0 && <p className="mt-1 text-[13px] text-muted">{names.slice(0, 12).join(", ")}{names.length > 12 ? ` and ${names.length - 12} more` : ""}</p>}
    </li>
  );
}

/** What the generator actually did about fairness, exactly as stored with the version. */
function SolverFairness({ report }: { report: WorkloadReport }) {
  const stored = report.fairness.stored_assessment;
  return (
    <Card title="Fairness in timetable generation" description="Whether workload fairness was an optimization objective for this version">
      <div className="space-y-3 px-5 py-5 text-sm">
        {!stored || !stored.enabled ? (
          <p className="text-ink">
            Workload fairness was <strong>not</strong> an optimization objective for this version. The figures below describe the
            result; the generator did not aim for them.
          </p>
        ) : (
          <>
            <p className="text-ink">
              The generator optimized workload stages in priority order ({stored.strategy.toLowerCase().replaceAll("_", " ")}).{" "}
              {stored.all_stages_proven_optimal ? "Every listed stage was proven optimal." : "Not every stage was proven optimal."}{" "}
              {stored.priority_is_development_configuration && "The priority order is a development configuration, not university policy."}
            </p>
            <ol className="grid gap-1 sm:grid-cols-2">
              {stored.priority_order.map((criterion) => {
                const stage = stored.stages.find((row) => row.criterion === criterion);
                return (
                  <li key={criterion} className="flex items-center justify-between gap-3 text-[13px]">
                    <span className="text-ink">{STAGES[criterion] ?? criterion}</span>
                    {stage?.status === "OPTIMAL" ? (
                      <Badge tone="accent">Proven</Badge>
                    ) : stage ? (
                      <Badge tone="primary">{stage.status === "FEASIBLE" ? "Feasible, not proven" : stage.status.toLowerCase()}</Badge>
                    ) : (
                      <Badge tone="neutral">Not reached</Badge>
                    )}
                  </li>
                );
              })}
            </ol>
          </>
        )}
      </div>
    </Card>
  );
}
