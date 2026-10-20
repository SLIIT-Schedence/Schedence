// Test-only public session data. No API requests or personal student records.
export const metadata = {
  periodCode: "TEST-FINAL", periodName: "Faculty of Computing - Final Examination",
  academicYearLabel: "2026/2027", semester: 2, examKind: "FINAL", status: "UNSAVED",
  recordedAt: "2026-10-19T08:00:00Z", recordedAtLabel: "Generated",
  calendarStart: "2026-11-02", calendarEnd: "2026-11-30",
};
export const emptyFilters = { year: "", semester: "", specialization: "", module: "" };
export const sessions = Array.from({ length: 70 }, (_, index) => ({
  academic_year: index < 60 ? 1 : 2, semester: index < 60 ? 1 : 2,
  specializations: index < 60 ? ["Data Science", "Software Engineering"] : ["Information Technology"],
  date: `2026-11-${String(2 + index % 20).padStart(2, "0")}`, start_time: "08:30:00", end_time: "11:00:00",
  module_code: `TEST${String(index).padStart(3, "0")}`,
  module_name: index === 0 ? "Advanced Research Methods and Professional Practice with a deliberately long examination module title to verify line wrapping and readability across professional A4 timetable exports" : `Examination Module ${index}`,
  session_number: index === 0 ? 2 : 1, session_count: index === 0 ? 2 : 1,
  candidates: 15, normal_candidates: 12, repeat_candidates: 3,
  rooms: [{ venue_id: "HALL-A", venue_name: "Main Examination Hall - East Wing", seats: 10 }, { venue_id: "HALL-B", venue_name: "Computing Laboratory B", seats: 5 }],
}));
