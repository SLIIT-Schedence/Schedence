import type { ExamVenueDetails, SavedTimetableDetail } from "@/lib/examination";

export const VENUE_PAGE_SIZE = 25;
export const VENUE_COLUMNS = {
  building: "Building", venue_type: "Type", normal_capacity: "Capacity", exam_capacity: "Effective exam capacity",
  available_for_exams: "Exam availability",
} as const;
export type VenueColumn = keyof typeof VENUE_COLUMNS;
export type VenueFilters = { q: string; building: string; type: string; availability: string; review: string; timetable: string };
type Params = Record<string, string | string[] | undefined>;
const first = (value: Params[string]) => (Array.isArray(value) ? value[0] : value) ?? "";
export const nonNegative = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
export const recordedText = (value: unknown): value is string => typeof value === "string" && Boolean(value.trim());
export const validVenueId = (value: string) => /^[A-Za-z0-9+](?:[A-Za-z0-9+ -]{0,14}[A-Za-z0-9+])?$/.test(value);
export const validTimetableId = (value: string) => /^[1-9][0-9]{0,9}$/.test(value) && Number(value) <= 2_147_483_647;

export function venueValue(venue: ExamVenueDetails, field: VenueColumn): string | null {
  const value = venue[field];
  if (field === "available_for_exams") return typeof value === "boolean" ? (value ? "Available" : "Unavailable") : null;
  if (field === "normal_capacity" || field === "exam_capacity") return nonNegative(value) ? value.toLocaleString("en-GB") : null;
  return recordedText(value) ? value : null;
}

export function missingVenueFields(venue: ExamVenueDetails): string[] {
  return [...(recordedText(venue.venue_name) ? [] : ["Room name"]),
    ...(Object.keys(VENUE_COLUMNS) as VenueColumn[]).filter((field) => venueValue(venue, field) === null).map((field) => VENUE_COLUMNS[field])];
}

export type VenueAssignment = {
  sessionId: string; moduleCode: string; moduleName: string; date: string; startTime: string; endTime: string;
  seats: number | null; capacity: number | null; percentage: number | null; underused: boolean; overCapacity: boolean;
};

/** Saved room capacities, not today's catalogue or normal capacities, are the denominator. */
export function venueAssignments(timetable: SavedTimetableDetail | null): Map<string, VenueAssignment[]> | null {
  if (!timetable || !Array.isArray(timetable.sessions)) return null;
  const assignments = new Map<string, VenueAssignment[]>();
  for (const session of timetable.sessions) {
    if (!Array.isArray(session.rooms)) return null;
    for (const room of session.rooms) {
      if (!recordedText(room.venue_id)) return null;
      const seats = nonNegative(room.seats) ? room.seats : null;
      const capacity = nonNegative(room.exam_capacity) ? room.exam_capacity : null;
      const percentage = seats !== null && capacity !== null && capacity > 0 ? seats / capacity * 100 : null;
      const entry = { sessionId: session.session_id, moduleCode: session.module_code, moduleName: session.module_name,
        date: session.date, startTime: session.start_time, endTime: session.end_time, seats, capacity, percentage,
        underused: percentage !== null && percentage < 50,
        overCapacity: seats !== null && capacity !== null && seats > capacity };
      assignments.set(room.venue_id, [...(assignments.get(room.venue_id) ?? []), entry]);
    }
  }
  return assignments;
}

export function venueOptions(venues: ExamVenueDetails[]) {
  return {
    buildings: [...new Set(venues.map((venue) => venue.building).filter(recordedText))].sort(),
    types: [...new Set(venues.map((venue) => venue.venue_type).filter(recordedText))].sort(),
  };
}

export function readVenueFilters(params: Params): VenueFilters {
  return { q: first(params.q).trim(), building: first(params.building), type: first(params.type),
    availability: ["yes", "no", "unknown"].includes(first(params.availability)) ? first(params.availability) : "",
    review: ["missing", "underuse"].includes(first(params.review)) ? first(params.review) : "",
    // Preserve invalid input for an explicit error, rather than silently inspecting a different timetable.
    timetable: first(params.timetable) };
}

export function filterVenues(venues: ExamVenueDetails[], filters: VenueFilters, assignments: Map<string, VenueAssignment[]> | null) {
  const q = filters.q.toLowerCase();
  return venues.filter((venue) => (!q || [venue.venue_id, venue.venue_name, venue.building].some((value) => recordedText(value) && value.toLowerCase().includes(q)))
    && (!filters.building || venue.building === filters.building)
    && (!filters.type || venue.venue_type === filters.type)
    && (!filters.availability || (filters.availability === "unknown" ? typeof venue.available_for_exams !== "boolean"
      : venue.available_for_exams === (filters.availability === "yes")))
    && (filters.review !== "missing" || missingVenueFields(venue).length > 0)
    && (filters.review !== "underuse" || Boolean(assignments?.get(venue.venue_id)?.some((item) => item.underused))))
    .sort((a, b) => a.venue_id.localeCompare(b.venue_id));
}
