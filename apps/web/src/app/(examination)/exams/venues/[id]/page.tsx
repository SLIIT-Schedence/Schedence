import Form from "next/form";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense, type ReactNode } from "react";
import { Badge, Card, EmptyState, PageHeader, PageSkeleton, query } from "@/components/console/ui";
import { requireExaminationAdmin } from "@/lib/examination-access";
import { getSavedTimetable, listExamVenueBlocks, listExamVenues, listVenueTimetables } from "@/lib/examination";
import { missingVenueFields, nonNegative, readVenueFilters, validTimetableId, validVenueId, venueAssignments, venueValue, VENUE_COLUMNS } from "@/lib/examination-venues";
import { ExaminationReadError } from "../../_components/read-error";
import { RefreshButton } from "../../_components/refresh-button";
import { buttonClass, TimetableSelect, UsageExplanation, UsageSummary, Value } from "../_components/venue-ui";

export const metadata = { title: "Venue details" };
type Props = { params: Promise<{ id: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> };

export default function VenueDetailsPage(props: Props) {
  return <div className="space-y-6"><Link href="/exams/venues" className="text-sm font-semibold text-primary-strong hover:underline">Back to venues</Link>
    <Suspense fallback={<PageSkeleton />}><VenueDetails {...props} /></Suspense></div>;
}

async function VenueDetails({ params, searchParams }: Props) {
  await requireExaminationAdmin();
  const { id } = await params;
  if (!validVenueId(id)) notFound();
  const result = await listExamVenues();
  if (!result.ok) return <Card><ExaminationReadError error={result.error} what="this venue" /></Card>;
  const venue = result.data.find((entry) => entry.venue_id === id);
  if (!venue) notFound();
  const filters = readVenueFilters(await searchParams);
  const [timetables, selected, blocks] = await Promise.all([listVenueTimetables(),
    validTimetableId(filters.timetable) ? getSavedTimetable(Number(filters.timetable)) : null, listExamVenueBlocks(id)]);
  const assignments = venueAssignments(selected?.ok ? selected.data : null);
  const rows = assignments === null ? null : assignments.get(id) ?? [];
  const missing = missingVenueFields(venue);
  const details: [string, ReactNode][] = [
    ["Room name", <Value key="name" value={venue.venue_name} />],
    ...Object.entries(VENUE_COLUMNS).map(([key, label]): [string, ReactNode] => [label, <Value key={key} value={venueValue(venue, key as keyof typeof VENUE_COLUMNS)} />]),
    ["Floor", <Value key="floor" value={typeof venue.floor === "number" && Number.isSafeInteger(venue.floor) ? venue.floor : null} />],
    ["Exam seating percentage", <Value key="percentage" value={nonNegative(venue.exam_seating_percentage) && venue.exam_seating_percentage >= 1 && venue.exam_seating_percentage <= 100 ? `${venue.exam_seating_percentage}%` : null} />],
    ["Seating rule", <Value key="rule" value={venue.exam_seating_rule} />],
  ];
  return <>
    <PageHeader title={venue.venue_id} description={venue.venue_name?.trim() || "Room name not available"}
      actions={<><Badge tone="neutral">Read only</Badge><RefreshButton label="Refresh" /></>} />
    {missing.length > 0 && <p className="rounded-xl bg-accent p-4 text-sm text-accent-ink">Missing data: {missing.join(", ")}.</p>}
    <Card title="Room details"><dl className="grid sm:grid-cols-2 lg:grid-cols-3">{details.map(([label, value]) => <div key={label} className="border-b border-line px-5 py-4">
      <dt className="text-xs font-semibold text-muted">{label}</dt><dd className="mt-2 text-sm text-ink">{value}</dd>
    </div>)}</dl></Card>
    <Card title="Assigned exams" description="Inspect assignments and seating usage in one saved timetable.">
      <Form action={`/exams/venues/${encodeURIComponent(id)}`} key={filters.timetable} className="grid items-end gap-3 border-b border-line p-5 sm:grid-cols-[1fr_auto]">
        <TimetableSelect result={timetables} selected={filters.timetable} /><button type="submit" className={buttonClass}>View assignments</button>
      </Form>
      {!timetables.ok && <ExaminationReadError error={timetables.error} what="assignment timetable choices" />}
      {selected && !selected.ok && <ExaminationReadError error={selected.error} what="assigned exams for this timetable" />}
      <div className="space-y-3 p-5"><UsageExplanation />
        {selected?.ok && <p className="text-sm text-ink"><Link className="font-semibold text-primary-strong hover:underline" href={`/exams/timetables/${selected.data.timetable_id}`}>
          {selected.data.period_name} · {selected.data.exam_kind} · Draft #{selected.data.draft_number} · {selected.data.status} · ID {selected.data.timetable_id}
        </Link></p>}
        <div className="text-sm"><UsageSummary assignments={rows} /></div>
      </div>
      {rows === null ? <EmptyState title={filters.timetable ? "Assignments unavailable" : "Select a saved timetable"}>Choose a valid saved timetable with readable assignments to inspect this room. Unknown assignments are not counted as zero.</EmptyState>
        : !rows.length ? <EmptyState title="No assigned exam sessions in this timetable">This applies only to the selected saved version.</EmptyState>
        : <div className="overflow-x-auto"><table className="w-full min-w-[45rem] text-left">
          <caption className="sr-only">Exam sessions assigned to this room in the selected timetable</caption>
          <thead className="border-b border-line bg-background text-xs text-muted"><tr>{["Module", "Date", "Time", "Assigned seats", "Saved exam capacity", "Seat usage"].map((label) => <th key={label} scope="col" className="px-4 py-3 font-semibold">{label}</th>)}</tr></thead>
          <tbody className="divide-y divide-line">{rows.map((entry) => <tr key={entry.sessionId}>
            <th scope="row" className="px-4 py-3 text-sm font-medium"><Value value={entry.moduleCode} /><span className="block font-normal text-muted"><Value value={entry.moduleName} /></span></th>
            <td className="px-4 py-3 text-sm text-muted"><Value value={entry.date} /></td>
            <td className="px-4 py-3 text-sm text-muted"><Value value={entry.startTime} /> – <Value value={entry.endTime} /></td>
            <td className="px-4 py-3 text-sm"><Value value={entry.seats} /></td><td className="px-4 py-3 text-sm"><Value value={entry.capacity} /></td>
            <td className="space-y-1 px-4 py-3 text-sm"><Value value={entry.percentage === null ? null : `${entry.percentage.toLocaleString("en-GB", { maximumFractionDigits: 1 })}%`} />
              {entry.underused && <div><Badge tone="accent">Possible underuse</Badge></div>}{entry.overCapacity && <div><Badge tone="danger">Over saved capacity</Badge></div>}</td>
          </tr>)}</tbody>
        </table></div>}
      <div className="p-5"><Link className="text-sm font-semibold text-primary-strong hover:underline" href={`/exams/venues${query({ timetable: filters.timetable })}`}>Compare rooms in this timetable</Link></div>
    </Card>
    <Card title="Recorded availability blocks" description="Recorded teaching, maintenance or other periods when this room cannot be used for exams.">
      {!blocks.ok ? <ExaminationReadError error={blocks.error} what="availability blocks" />
        : !blocks.data.length ? <EmptyState title="No recorded availability blocks">This does not guarantee availability for every date or timetable.</EmptyState>
        : <div className="overflow-x-auto"><table className="w-full min-w-[40rem] text-left"><caption className="sr-only">Recorded availability blocks for this room</caption>
          <thead className="border-b border-line bg-background text-xs text-muted"><tr>{["Date", "Time", "Source", "Reason"].map((label) => <th key={label} scope="col" className="px-4 py-3 font-semibold">{label}</th>)}</tr></thead>
          <tbody className="divide-y divide-line">{blocks.data.map((entry, index) => <tr key={`${entry.block_date}-${entry.start_time}-${index}`}>
            <td className="px-4 py-3 text-sm"><Value value={entry.block_date} /></td><td className="px-4 py-3 text-sm"><Value value={entry.start_time} /> – <Value value={entry.end_time} /></td>
            <td className="px-4 py-3 text-sm"><Value value={entry.source} /></td><td className="px-4 py-3 text-sm"><Value value={entry.reason} /></td>
          </tr>)}</tbody>
        </table></div>}
    </Card>
  </>;
}
