import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { Badge, Card, EmptyState, formatDate, formatDateTime, PageHeader, TableSkeleton } from "@/components/console/ui";
import { generateSeating, saveSeatingDraft } from "@/lib/duty-allocation-actions";
import {
  getDutyStatus, getRoomSeats, getSeatingRun, ID_TEXT_PATTERN, listDutySessions, listSeatingDrafts, resolvePeriod, RUN_PATTERN,
} from "@/lib/duty-allocation";
import { clock, roomKey, titleCase, type DutySession, type SeatingRun } from "@/lib/duty-allocation-types";
import { RefreshButton } from "../../_components/refresh-button";
import { ActionForm } from "../_components/action-form";
import {
  cell, DataNotice, DutyReadError, first, headCell, Notice, NoPeriods, number, PeriodBar, ViolationList, type SearchParams,
} from "../_components/duty-ui";

export const metadata: Metadata = { title: "Student seating" };

export default function SeatingPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Student seating"
        description="Seat students by IT number inside the exam rooms of the timetable. Seats are logical until a verified room layout exists."
        actions={<RefreshButton label="Refresh" />}
      />
      <Suspense fallback={<Card><TableSkeleton rows={6} /></Card>}>
        <Seating searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Seating({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const seatingId = first(params.seating);
  const runResult = seatingId && RUN_PATTERN.test(seatingId) ? await getSeatingRun(seatingId) : undefined;
  const run = runResult?.ok ? runResult.data : undefined;
  const resolved = await resolvePeriod(run?.meta.period_code ?? first(params.period));
  if (!resolved.ok) return <Card><DutyReadError error={resolved.error} what="exam periods" /></Card>;
  const { period, periods, meta } = resolved.data;
  if (!period) return <Card><NoPeriods /></Card>;
  const sessions = await listDutySessions(period);
  const sessionOf = new Map(sessions.ok ? sessions.data.items.map((s) => [s.session_id, s]) : []);
  const session = first(params.session);
  const venue = first(params.venue);

  return (
    <>
      <DataNotice meta={meta} />
      <Card title="Generate a seating plan" description="Calculated for every session of the period. The result is a temporary preview until you save it.">
        <PeriodBar action="/exams/duties/seating" periods={periods} period={period} />
        <ActionForm action={generateSeating} submitLabel="Generate seating" pendingLabel="Seating students…" icon="meeting_room" className="p-5">
          <input type="hidden" name="period" value={period} />
        </ActionForm>
      </Card>

      {seatingId && runResult && !runResult.ok && (
        runResult.error.status === 404
          ? <Notice tone="warn" title="This seating plan is no longer available">Seating plans are temporary previews kept in memory. Generate it again.</Notice>
          : <Card><DutyReadError error={runResult.error} what="the seating plan" /></Card>
      )}

      {run && <SeatingResult run={run} period={period} sessionOf={sessionOf} />}
      {run && session && venue && ID_TEXT_PATTERN.test(session) && ID_TEXT_PATTERN.test(venue) && (
        <Suspense fallback={<Card><TableSkeleton rows={6} /></Card>}>
          <RoomSeats seatingId={run.seating_id} sessionId={session} venueId={venue} session={sessionOf.get(session)} />
        </Suspense>
      )}
      <Suspense fallback={null}>
        <SavedDrafts period={period} />
      </Suspense>
    </>
  );
}

function SeatingResult({ run, period, sessionOf }: { run: SeatingRun; period: string; sessionOf: Map<string, DutySession> }) {
  const rooms = [...run.rooms].sort((a, b) => (sessionOf.get(a.session_id)?.start ?? a.session_id).localeCompare(sessionOf.get(b.session_id)?.start ?? b.session_id));
  const href = (sessionId: string, venueId: string) =>
    `/exams/duties/seating?period=${encodeURIComponent(period)}&seating=${run.seating_id}&session=${encodeURIComponent(sessionId)}&venue=${encodeURIComponent(venueId)}`;
  return (
    <Card
      title="Seating plan"
      description={`${number.format(run.assignment_count)} students seated in ${number.format(run.rooms.length)} rooms · ${formatDateTime(run.created_at)} · temporary preview`}
      actions={<Badge tone={run.seat_state === "VERIFIED" ? "accent" : "neutral"}>{titleCase(run.seat_state)} seats</Badge>}
    >
      <div className="space-y-4 p-5">
        {run.seat_state !== "VERIFIED" && (
          <Notice tone="warn">
            Seat numbers are logical. {run.synthetic_halls.length > 0 && `${run.synthetic_halls.length} rooms use synthetic demo layouts. `}
            {run.provisional_halls.length > 0 && `${run.provisional_halls.length} rooms have no verified layout yet. `}
            Check the rooms before seats are given to students.
          </Notice>
        )}
        {run.violations.length > 0 && <Notice tone="danger" title="Some students could not be seated"><ViolationList violations={run.violations} /></Notice>}
        {run.example_problems.length > 0 && <Notice tone="warn"><ul className="list-disc space-y-1 pl-5">{run.example_problems.map((p) => <li key={p}>{p}</li>)}</ul></Notice>}
        {run.capacity_rules_source && <p className="text-xs text-muted">Exam capacity rules: {run.capacity_rules_source}</p>}
        <SaveSeating run={run} />
      </div>
      <div className="overflow-x-auto border-t border-line">
        <table className="w-full min-w-[52rem] text-left">
          <caption className="sr-only">Rooms in the seating plan</caption>
          <thead className="border-b border-line bg-background text-xs text-muted">
            <tr>{["Session", "Room", "Students", "Exam capacity", "Usable seats", ""].map((label, i) => <th key={i} scope="col" className={headCell}>{label}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rooms.map((room) => {
              const s = sessionOf.get(room.session_id);
              const over = room.seat_limit !== null && room.students > room.seat_limit;
              return (
                <tr key={roomKey(room.session_id, room.venue_id)}>
                  <td className={cell}>
                    {s ? <><span className="font-medium text-ink">{formatDate(s.date)} · {clock(s.start)}–{clock(s.end)}</span><span className="block font-mono text-xs">{s.module_code}</span></> : <span className="font-mono text-xs">{room.session_id}</span>}
                  </td>
                  <td className={cell}><span className="text-ink">{room.venue_id}</span><span className="block text-xs">{room.venue_type.replaceAll("_", " ")}</span></td>
                  <td className={`${cell} tabular-nums`}>{number.format(room.students)}</td>
                  <td className={`${cell} whitespace-nowrap`}>
                    {room.exam_capacity !== null ? <>{room.exam_capacity}<span className="block text-xs">{room.capacity_percentage}% of {room.full_capacity}</span></> : <span className="text-xs">No capacity rule</span>}
                  </td>
                  <td className={`${cell} tabular-nums`}>{number.format(room.usable_seats)}{over && <span className="ml-2"><Badge tone="danger">Over limit</Badge></span>}</td>
                  <td className={`${cell} text-right`}>
                    <Link href={href(room.session_id, room.venue_id)} className="font-semibold text-primary-strong underline-offset-2 hover:underline">View seats</Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

async function SaveSeating({ run }: { run: SeatingRun }) {
  const status = await getDutyStatus();
  if (!status.ok || status.data.persistence_mode === "DISABLED") {
    return <p className="text-[13px] text-muted">Saving seating plans is turned off in the Examination Service (M3_DEMO_PERSISTENCE).</p>;
  }
  return (
    <ActionForm action={saveSeatingDraft.bind(null, run.seating_id)} submitLabel="Save as a seating draft" pendingLabel="Saving…" icon="save" variant="secondary"
      disabledReason={run.violations.length ? "Every student must be seated before the plan can be saved." : undefined} />
  );
}

async function RoomSeats({ seatingId, sessionId, venueId, session }: { seatingId: string; sessionId: string; venueId: string; session?: DutySession }) {
  const result = await getRoomSeats(seatingId, sessionId, venueId);
  if (!result.ok) return <Card><DutyReadError error={result.error} what="the room's seats" /></Card>;
  const seats = [...result.data.seats].sort((a, b) => a.seat_number - b.seat_number);
  return (
    <Card
      title={`Seats in ${venueId}`}
      description={session ? `${session.module_code} · ${formatDate(session.date)} ${clock(session.start)}–${clock(session.end)} · ${number.format(result.data.students)} students` : `${number.format(result.data.students)} students`}
      actions={<Badge tone="neutral">{titleCase(result.data.seat_state)}</Badge>}
    >
      {!seats.length ? <EmptyState icon="meeting_room" title="No students in this room" /> : (
        <div className="max-h-[32rem] overflow-auto">
          <table className="w-full min-w-[36rem] text-left">
            <caption className="sr-only">Seat for each student in this room</caption>
            <thead className="sticky top-0 border-b border-line bg-background text-xs text-muted">
              <tr>{["Seat", "IT number", "Module", "Seat checked"].map((label) => <th key={label} scope="col" className={headCell}>{label}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-line">
              {seats.map((seat) => (
                <tr key={`${seat.student_id}-${seat.seat_number}`}>
                  <td className={`${cell} font-mono text-ink`}>{seat.seat_label}</td>
                  <td className={`${cell} font-mono`}>{seat.student_id}</td>
                  <td className={`${cell} font-mono`}>{seat.module_code}</td>
                  <td className={cell}>{seat.seat_verified ? "Verified layout" : "Logical seat"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

async function SavedDrafts({ period }: { period: string }) {
  const result = await listSeatingDrafts(period);
  if (!result.ok) return result.error.status === 503 ? null : <Card title="Saved seating drafts"><DutyReadError error={result.error} what="saved seating drafts" /></Card>;
  return (
    <Card title="Saved seating drafts" description={result.data.persistence.durable ? "Stored in the database." : result.data.persistence.notice}>
      {!result.data.items.length ? <p className="px-5 py-4 text-sm text-muted">No seating drafts saved for this period.</p> : (
        <ul className="divide-y divide-line">
          {result.data.items.map((d) => (
            <li key={d.draft_id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 text-sm">
              <span><span className="font-semibold text-ink">Version {d.version}</span><span className="block text-xs text-muted">{formatDateTime(d.created_at)} · {d.notice}</span></span>
              <span className="flex flex-wrap gap-1.5"><Badge tone={d.status === "DRAFT" ? "primary" : "neutral"}>{titleCase(d.status)}</Badge><Badge tone="neutral">{titleCase(d.seat_state)}</Badge></span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
