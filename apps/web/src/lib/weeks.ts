// Calendar weeks in Colombo time (UTC+05:30, no daylight saving), starting on Monday.

const DAY = 24 * 60 * 60 * 1000;
const WEEK = 7 * DAY;
const COLOMBO_OFFSET = 5.5 * 60 * 60 * 1000;

const shortDay = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "Asia/Colombo" });
const longDay = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Colombo" });

export type WeekBucket = { key: string; start: number; label: string; title: string };

/** The last `count` weeks, oldest first, the current (partial) week included. Request-time only. */
export function recentWeeks(count: number): WeekBucket[] {
  const local = Date.now() + COLOMBO_OFFSET;
  const midnight = Math.floor(local / DAY) * DAY;
  const sinceMonday = (new Date(midnight).getUTCDay() + 6) % 7;
  const currentStart = midnight - sinceMonday * DAY - COLOMBO_OFFSET;
  return Array.from({ length: count }, (_, i) => {
    const start = currentStart - (count - 1 - i) * WEEK;
    return {
      key: new Date(start).toISOString(),
      start,
      label: shortDay.format(start),
      title: i === count - 1 ? `This week (from ${shortDay.format(start)})` : `Week of ${longDay.format(start)}`,
    };
  });
}

/** How many of the timestamps fall into each week. */
export function countByWeek(weeks: WeekBucket[], timestamps: string[]): number[] {
  const counts = weeks.map(() => 0);
  const first = weeks[0]?.start ?? 0;
  for (const timestamp of timestamps) {
    const index = Math.floor((Date.parse(timestamp) - first) / WEEK);
    if (index >= 0 && index < counts.length) counts[index] += 1;
  }
  return counts;
}
