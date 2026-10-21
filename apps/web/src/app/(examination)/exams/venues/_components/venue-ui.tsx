import Link from "next/link";
import { Badge } from "@/components/console/ui";
import type { ExaminationResult } from "@/lib/examination-api";
import type { Page, SavedTimetable } from "@/lib/examination";
import type { VenueAssignment } from "@/lib/examination-venues";

export const fieldClass = "mt-1 h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm text-ink focus:border-primary";
export const buttonClass = "inline-flex h-10 items-center justify-center rounded-xl border border-line px-4 text-sm font-semibold text-ink hover:bg-background";
export function MissingValue() { return <Badge tone="accent">Not available</Badge>; }
export function Value({ value }: { value: string | number | null | undefined }) {
  return value === null || value === undefined || (typeof value === "string" && !value.trim()) ? <MissingValue /> : value;
}
export function ClearVenues() { return <Link href="/exams/venues" className={buttonClass}>Clear filters</Link>; }

export function TimetableSelect({ result, selected }: { result: ExaminationResult<Page<SavedTimetable>>; selected: string }) {
  const options = result.ok ? result.data.items : [];
  return <label className="text-xs font-semibold text-muted">Assignment timetable
    <select name="timetable" defaultValue={selected} className={fieldClass}>
      <option value="">Select a saved timetable</option>
      {selected && !options.some((item) => String(item.timetable_id) === selected) && <option value={selected}>Timetable #{selected}</option>}
      {options.map((item) => <option key={item.timetable_id} value={item.timetable_id}>
        {item.period_name} · {item.exam_kind} · Draft #{item.draft_number} · {item.status} · ID {item.timetable_id}
      </option>)}
    </select>
    <span className="mt-1 block font-normal">{result.ok ? result.data.total === 0 ? "No saved timetables returned."
      : result.data.total > options.length ? `Showing the ${options.length} most recent saved timetables.` : "Inspect one saved version at a time."
      : "Timetable choices could not be loaded."}</span>
  </label>;
}

export function UsageSummary({ assignments }: { assignments: VenueAssignment[] | null }) {
  if (assignments === null) return <MissingValue />;
  const low = assignments.filter((entry) => entry.underused).length;
  const over = assignments.filter((entry) => entry.overCapacity).length;
  return <div className="space-y-1"><span>{assignments.length} assigned exam {assignments.length === 1 ? "session" : "sessions"}</span>
    {low > 0 && <div><Badge tone="accent">Possible underuse: {low}</Badge></div>}
    {over > 0 && <div><Badge tone="danger">Over saved capacity: {over}</Badge></div>}
    {assignments.some((entry) => entry.percentage === null) && <div className="text-xs text-muted">Usage data incomplete</div>}
  </div>;
}

export function UsageExplanation() {
  return <p className="text-xs leading-5 text-muted">Assigned exams refer only to the selected timetable and count sessions per room. Possible underuse means fewer than 50% of the saved exam seats are assigned in a session; it is a review hint, not an optimization result. Usage uses the capacity saved with that timetable, which may differ from today&apos;s effective capacity. Availability reflects exam seating eligibility; date-specific blocks are shown in room details.</p>;
}
