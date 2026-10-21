import Form from "next/form";
import Link from "next/link";
import { Suspense } from "react";
import { Badge, Card, EmptyState, PageHeader, Pagination, pageNumber, query, TableSkeleton } from "@/components/console/ui";
import { requireExaminationAdmin } from "@/lib/examination-access";
import { getSavedTimetable, listExamVenues, listVenueTimetables } from "@/lib/examination";
import { filterVenues, missingVenueFields, readVenueFilters, validTimetableId, venueAssignments, venueOptions, venueValue, VENUE_COLUMNS, VENUE_PAGE_SIZE } from "@/lib/examination-venues";
import { ExaminationReadError } from "../_components/read-error";
import { RefreshButton } from "../_components/refresh-button";
import { buttonClass, ClearVenues, fieldClass, TimetableSelect, UsageExplanation, UsageSummary, Value } from "./_components/venue-ui";

export const metadata = { title: "Examination venues" };
type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default function VenuesPage({ searchParams }: { searchParams: SearchParams }) {
  return <div className="space-y-6">
    <PageHeader title="Venues" description="Browse existing examination rooms, seating capacity and saved exam assignments."
      actions={<><Badge tone="neutral">Read only</Badge><RefreshButton label="Refresh" /></>} />
    <Card><Suspense fallback={<TableSkeleton />}><VenueCatalog searchParams={searchParams} /></Suspense></Card>
  </div>;
}

async function VenueCatalog({ searchParams }: { searchParams: SearchParams }) {
  await requireExaminationAdmin();
  const params = await searchParams;
  const filters = readVenueFilters(params);
  const [result, timetables, selected] = await Promise.all([listExamVenues(), listVenueTimetables(),
    validTimetableId(filters.timetable) ? getSavedTimetable(Number(filters.timetable)) : null]);
  if (!result.ok) return <ExaminationReadError error={result.error} what="venues" />;
  const assignments = venueAssignments(selected?.ok ? selected.data : null);
  const options = venueOptions(result.data);
  const filtered = filterVenues(result.data, filters, assignments);
  const page = pageNumber(params.page);
  const rows = filtered.slice((page - 1) * VENUE_PAGE_SIZE, page * VENUE_PAGE_SIZE);
  const href = (page: number) => `/exams/venues${query({ ...filters, page })}`;
  const assignmentQuery = query({ timetable: filters.timetable });
  const missing = result.data.filter((venue) => missingVenueFields(venue).length).length;
  return <>
    <Form action="/exams/venues" key={JSON.stringify(filters)} className="grid gap-3 border-b border-line p-5 sm:grid-cols-2 xl:grid-cols-3">
      <label className="text-xs font-semibold text-muted">Search rooms<input type="search" name="q" defaultValue={filters.q} placeholder="Room code, name or building" className={fieldClass} /></label>
      {([['building', 'Building', options.buildings], ['type', 'Room type', options.types]] as const).map(([key, label, values]) =>
        <label key={key} className="text-xs font-semibold text-muted">{label}<select name={key} defaultValue={filters[key]} className={fieldClass}>
          <option value="">All {key === "building" ? "buildings" : "types"}</option>
          {filters[key] && !values.includes(filters[key]) && <option value={filters[key]}>{filters[key]} (unavailable)</option>}
          {values.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>)}
      <label className="text-xs font-semibold text-muted">Availability<select name="availability" defaultValue={filters.availability} className={fieldClass}>
        <option value="">All availability</option><option value="yes">Available</option><option value="no">Unavailable</option><option value="unknown">Not available in source</option>
      </select></label>
      <TimetableSelect result={timetables} selected={filters.timetable} />
      <label className="text-xs font-semibold text-muted">Review<select name="review" defaultValue={filters.review} className={fieldClass}>
        <option value="">All rooms</option><option value="missing">Missing data</option><option value="underuse">Possible underuse in selected timetable</option>
      </select></label>
      <div className="flex flex-wrap gap-3 sm:col-span-2 xl:col-span-3">
        <button type="submit" className="h-10 rounded-xl bg-primary px-4 text-sm font-semibold text-white hover:bg-primary-strong">Apply filters</button><ClearVenues />
      </div>
    </Form>
    <div className="space-y-2 border-b border-line px-5 py-4 text-sm text-muted">
      <p role="status">{filtered.length} of {result.data.length} rooms match. {missing > 0 && <Badge tone="accent">{missing} with missing data</Badge>}</p>
      {selected?.ok && <p>Assignments: {selected.data.period_name} · {selected.data.exam_kind} · Draft #{selected.data.draft_number} · {selected.data.status} · ID {selected.data.timetable_id}</p>}
      {!filters.timetable && <p>Select a saved timetable to view assigned exams and possible room underuse.</p>}
      {filters.timetable && !validTimetableId(filters.timetable) && <p role="alert">Select a valid saved timetable. Assignment data is unavailable.</p>}
      {selected?.ok && assignments === null && <p role="alert">Assignment data is incomplete. Counts and usage are unavailable.</p>}
      <UsageExplanation />
    </div>
    {!timetables.ok && <ExaminationReadError error={timetables.error} what="assignment timetable choices" />}
    {selected && !selected.ok && <ExaminationReadError error={selected.error} what="assigned exams for this timetable" />}
    {!result.data.length ? <EmptyState icon="meeting_room" title="No examination venues available">No venues were returned by the Examination Service.</EmptyState>
      : filters.review === "underuse" && assignments === null ? <EmptyState title="Room usage cannot be assessed">Select a timetable with readable room assignments before filtering for possible underuse.</EmptyState>
      : !filtered.length ? <EmptyState icon="filter_alt_off" title="No rooms match these filters" action={<ClearVenues />}>Try another room, building, type or review filter.</EmptyState>
      : !rows.length ? <EmptyState title="This page is empty" action={<Link className={buttonClass} href={href(1)}>Go to the first page</Link>}>Choose the first page to see matching rooms.</EmptyState>
      : <><div className="overflow-x-auto"><table className="w-full min-w-[65rem] text-left">
        <caption className="sr-only">Examination venues and assignments in the selected saved timetable</caption>
        <thead className="border-b border-line bg-background text-xs text-muted"><tr>
          {["Room code", ...Object.values(VENUE_COLUMNS), "Assigned exams"].map((label) => <th key={label} scope="col" className="px-4 py-3 font-semibold">{label}</th>)}
        </tr></thead>
        <tbody className="divide-y divide-line">{rows.map((venue) => <tr key={venue.venue_id} className="align-top">
          <th scope="row" className="px-4 py-4 text-sm"><Link href={`/exams/venues/${encodeURIComponent(venue.venue_id)}${assignmentQuery}`} prefetch={false}
            aria-label={`View details for room ${venue.venue_id}`} className="font-mono font-semibold text-primary-strong hover:underline">{venue.venue_id}</Link>
            <span className="mt-1 block font-normal text-muted"><Value value={venue.venue_name} /></span></th>
          {(Object.keys(VENUE_COLUMNS) as (keyof typeof VENUE_COLUMNS)[]).map((key) => <td key={key} className="px-4 py-4 text-sm text-muted">
            {key === "available_for_exams" && venue.available_for_exams === false ? <Badge tone="accent">Unavailable</Badge> : <Value value={venueValue(venue, key)} />}
          </td>)}
          <td className="px-4 py-4 text-sm text-muted"><UsageSummary assignments={assignments === null ? null : assignments.get(venue.venue_id) ?? []} /></td>
        </tr>)}</tbody>
      </table></div><Pagination page={page} pageSize={VENUE_PAGE_SIZE} total={filtered.length} hrefFor={href} /></>}
  </>;
}
