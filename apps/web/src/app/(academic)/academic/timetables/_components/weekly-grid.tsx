import type { CSSProperties } from "react";
import { Icon } from "@/components/icon";
import { groupLabel, SESSION_TYPES, type TimetableSession, type TimetableSummary } from "@/lib/teaching";

// The weekly calendar: Monday to Sunday, positioned by each session's actual minutes. Server-rendered.

const DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"] as const;
const DAY_NAMES: Record<string, string> = {
  MONDAY: "Monday",
  TUESDAY: "Tuesday",
  WEDNESDAY: "Wednesday",
  THURSDAY: "Thursday",
  FRIDAY: "Friday",
  SATURDAY: "Saturday",
  SUNDAY: "Sunday",
};
/** Pixels per minute in the desktop grid. */
const SCALE = 1.1;
const shortDate = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

const clock = (minute: number) => `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
const dateLabel = (iso: string) => shortDate.format(new Date(`${iso}T00:00:00Z`));

/** Parallel sessions (such as lab subgroups) share the column side by side. */
function lanes(sessions: TimetableSession[]) {
  const placed: { session: TimetableSession; lane: number; lanes: number }[] = [];
  let cluster: typeof placed = [];
  let clusterEnd = -1;
  const close = () => {
    const count = Math.max(1, ...cluster.map((row) => row.lane + 1));
    for (const row of cluster) row.lanes = count;
    cluster = [];
  };
  for (const session of sessions.toSorted((a, b) => a.start_minute - b.start_minute || b.end_minute - a.end_minute)) {
    if (session.start_minute >= clusterEnd) {
      close();
      clusterEnd = -1;
    }
    const busy = new Set(cluster.filter((row) => row.session.end_minute > session.start_minute).map((row) => row.lane));
    let lane = 0;
    while (busy.has(lane)) lane++;
    const row = { session, lane, lanes: 1 };
    cluster.push(row);
    placed.push(row);
    clusterEnd = Math.max(clusterEnd, session.end_minute);
  }
  close();
  return placed;
}

export function WeeklyGrid({ version, sessions }: { version: TimetableSummary; sessions: TimetableSession[] }) {
  const windows = version.calendar.teaching_windows;
  const start = Math.min(...windows.map((row) => row.start_minute), ...sessions.map((row) => row.start_minute));
  const end = Math.max(...windows.map((row) => row.end_minute), ...sessions.map((row) => row.end_minute));
  const first = Math.floor(start / 60) * 60;
  const last = Math.ceil(end / 60) * 60;
  const height = (last - first) * SCALE;
  const dates = Object.fromEntries(windows.map((row) => [row.day, row.teaching_date]));
  const byDay = Object.fromEntries(DAYS.map((day) => [day, sessions.filter((row) => row.day === day)]));
  const hours = Array.from({ length: (last - first) / 60 + 1 }, (_, index) => first + index * 60);

  return (
    <>
      {/* Tablets and up: the calendar grid. */}
      <div className="hidden overflow-x-auto md:block">
        <div className="grid min-w-[56rem] grid-cols-[3.5rem_repeat(7,minmax(0,1fr))]">
          <div className="border-b border-line" />
          {DAYS.map((day) => (
            <div key={day} className="border-b border-l border-line px-2 py-2.5 text-center">
              <p className="text-[13px] font-semibold text-ink">{DAY_NAMES[day]}</p>
              <p className="text-xs text-muted">{dates[day] ? dateLabel(dates[day]) : "—"}</p>
            </div>
          ))}
          <div className="relative" style={{ height }}>
            {hours.map((minute) => (
              <span key={minute} className="absolute right-2 -translate-y-1/2 text-[11px] text-muted" style={{ top: (minute - first) * SCALE }}>
                {clock(minute)}
              </span>
            ))}
          </div>
          {DAYS.map((day) => (
            <div key={day} className="relative border-l border-line" style={{ height }}>
              {hours.map((minute) => (
                <span key={minute} aria-hidden="true" className="absolute inset-x-0 border-t border-line/70" style={{ top: (minute - first) * SCALE }} />
              ))}
              {lanes(byDay[day]).map(({ session, lane, lanes: count }) => (
                <SessionBlock
                  key={session.delivery_reference}
                  session={session}
                  style={{
                    top: (session.start_minute - first) * SCALE + 1,
                    height: (session.end_minute - session.start_minute) * SCALE - 2,
                    left: `calc(${(lane / count) * 100}% + 2px)`,
                    width: `calc(${100 / count}% - 4px)`,
                  }}
                />
              ))}
            </div>
          ))}
        </div>
      </div>

      {/* Phones: one list per day. */}
      <div className="divide-y divide-line md:hidden">
        {DAYS.filter((day) => byDay[day].length).map((day) => (
          <section key={day} className="px-4 py-4">
            <h3 className="text-sm font-semibold text-ink">
              {DAY_NAMES[day]} <span className="font-normal text-muted">{dates[day] ? dateLabel(dates[day]) : ""}</span>
            </h3>
            <ul className="mt-3 space-y-2">
              {byDay[day].map((session) => (
                <li key={session.delivery_reference} className="rounded-xl border border-line px-3 py-2.5">
                  <SessionText session={session} />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </>
  );
}

function SessionBlock({ session, style }: { session: TimetableSession; style: CSSProperties }) {
  const online = session.delivery_mode === "ONLINE";
  const subject = session.coverage[0];
  return (
    <div
      style={style}
      title={describe(session)}
      className={`absolute overflow-hidden rounded-lg border px-1.5 py-1 text-[11px] leading-tight ${
        online ? "border-accent-ink/30 bg-accent text-accent-ink" : "border-primary/30 bg-primary-soft text-primary-strong"
      }`}
    >
      <p className="truncate font-semibold">
        {subject?.module_code} · {SESSION_TYPES[subject?.session_type ?? ""] ?? subject?.session_type}
      </p>
      <p className="truncate">
        {session.start_time}–{session.end_time}
      </p>
      <p className="truncate">{online ? "Online" : (session.venue?.venue_name ?? "No venue")}</p>
      <p className="truncate text-ink/70">{session.lecturer.staff_name}</p>
    </div>
  );
}

/** Full session details: module, type, time, lecturer, venue or online, and the groups it covers. */
export function SessionText({ session }: { session: TimetableSession }) {
  const subject = session.coverage[0];
  const online = session.delivery_mode === "ONLINE";
  const groups = [...new Set(session.coverage.map((row) => groupLabel(row.group_id)))];
  return (
    <div className="min-w-0 text-[13px] leading-5">
      <p className="font-semibold text-ink">
        {subject?.module_code} {subject?.module_name}
      </p>
      <p className="text-muted">
        {SESSION_TYPES[subject?.session_type ?? ""] ?? subject?.session_type} · {session.start_time}–{session.end_time}
      </p>
      <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-muted">
        <span className="inline-flex items-center gap-1">
          <Icon name="school" size={16} />
          {session.lecturer.staff_name}
        </span>
        <span className="inline-flex items-center gap-1">
          <Icon name="meeting_room" size={16} />
          {online ? "Online" : (session.venue?.venue_name ?? "No venue")}
        </span>
        <span className="inline-flex items-center gap-1">
          <Icon name="group" size={16} />
          {groups.join(", ")}
        </span>
      </p>
    </div>
  );
}

function describe(session: TimetableSession) {
  const subject = session.coverage[0];
  return [
    `${subject?.module_code} ${subject?.module_name}`,
    `${SESSION_TYPES[subject?.session_type ?? ""] ?? subject?.session_type}, ${DAY_NAMES[session.day] ?? session.day} ${session.start_time}–${session.end_time}`,
    `Lecturer: ${session.lecturer.staff_name}`,
    session.delivery_mode === "ONLINE" ? "Online" : `Venue: ${session.venue?.venue_name ?? "none"}`,
    `Groups: ${[...new Set(session.coverage.map((row) => groupLabel(row.group_id)))].join(", ")}`,
  ].join("\n");
}
