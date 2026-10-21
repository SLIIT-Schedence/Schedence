import { Card, EmptyState } from "@/components/console/ui";
import { dayLabel, staffHours, teachingLoadByDay, workloadScale, type StaffTeachingVersion } from "@/lib/staff-detail";

export function WorkloadCharts({ version }: { version: StaffTeachingVersion }) {
  const row = version.workload;
  const days = teachingLoadByDay(version.sessions);
  const maximum = Math.max(...days.map((day) => day.minutes), 60);
  const scale = row ? workloadScale(row.assigned_teaching_hours, row.weekly_teaching_target_hours) : null;
  return <div className="grid items-start gap-6 lg:grid-cols-2">
    <Card title="Assigned hours vs target" description="Weekly teaching hours for the selected timetable version.">
      {!row || !scale ? <EmptyState icon="schedule" title="Workload report unavailable">This staff member has no workload entry in the selected version.</EmptyState> : <div className="space-y-5 p-5">
        <div role="img" aria-label={`Assigned teaching ${staffHours(row.assigned_teaching_hours)}. Target ${staffHours(row.weekly_teaching_target_hours)}.`} className="space-y-4">
          <HoursBar label="Assigned" value={staffHours(row.assigned_teaching_hours)} width={scale.assigned} />
          {scale.target !== null ? <HoursBar label="Target" value={staffHours(row.weekly_teaching_target_hours)} width={scale.target} target /> : <p className="text-sm text-muted">Target not available. A comparison cannot be shown.</p>}
        </div>
        <p className="text-sm text-muted">{row.difference_from_target_hours === null ? "Difference from target is not available." : `${row.difference_from_target_hours > 0 ? "+" : ""}${staffHours(row.difference_from_target_hours)} compared with this version’s stored target.`}</p>
        {row.weekly_teaching_cap_hours !== null && <p className="text-sm text-muted">Weekly cap: <span className="font-semibold text-ink">{staffHours(row.weekly_teaching_cap_hours)}</span></p>}
      </div>}
    </Card>
    <Card title="Teaching load by day" description="Session duration, with combined student groups counted once.">
      {!version.sessions.length ? <EmptyState icon="calendar_month" title="No teaching sessions">Daily teaching hours will appear when sessions are assigned in this version.</EmptyState> : <div className="space-y-3 p-5" role="img" aria-label={days.map((day) => `${dayLabel(day.day)} ${staffHours(day.minutes / 60)}`).join("; ")}>
        {days.map((day) => <div key={day.day} className="grid grid-cols-[5.5rem_minmax(0,1fr)_3.5rem] items-center gap-3 text-sm">
          <span className="text-muted">{dayLabel(day.day)}</span><div className="h-3 overflow-hidden rounded-full bg-background" aria-hidden="true"><div className="h-full rounded-full bg-primary" style={{ width: `${day.minutes / maximum * 100}%` }} /></div><span className="text-right font-semibold tabular-nums text-ink">{staffHours(day.minutes / 60)}</span>
        </div>)}
      </div>}
    </Card>
  </div>;
}
function HoursBar({ label, value, width, target = false }: { label: string; value: string; width: number; target?: boolean }) {
  return <div><div className="mb-2 flex justify-between gap-3 text-sm"><span className="text-muted">{label}</span><span className="font-semibold tabular-nums text-ink">{value}</span></div><div className="h-4 overflow-hidden rounded-full bg-background" aria-hidden="true"><div className={`h-full rounded-full ${target ? "bg-secondary" : "bg-primary"}`} style={{ width: `${width}%` }} /></div></div>;
}
