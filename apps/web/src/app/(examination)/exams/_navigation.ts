import type { ConsoleNavItem } from "@/components/console/shell";

export const EXAM_NAV: ConsoleNavItem[] = [
  { href: "/exams", label: "Overview", icon: "dashboard" },
  { href: "/exams/generate", label: "Generate Timetable", icon: "edit_note" },
  { href: "/exams/timetables", label: "Timetables", icon: "fact_check" },
  { href: "/exams/periods", label: "Exam Periods", icon: "history" },
  { href: "/exams/modules", label: "Modules", icon: "menu_book" },
  { href: "/exams/venues", label: "Venues", icon: "meeting_room" },
  { href: "/exams/registrations", label: "Registrations", icon: "how_to_reg" },
  { href: "/exams/settings", label: "Settings", icon: "admin_panel_settings" },
  // Fairness-Aware Staff Allocation for Examination Duties
  { href: "/exams/duties/overview", label: "Duty Overview", icon: "dashboard" },
  { href: "/exams/duties/staff", label: "Duty Staff", icon: "group", detailLabel: "Staff member" },
  { href: "/exams/duties/allocation", label: "Duty Allocation", icon: "work" },
  { href: "/exams/duties/fairness", label: "Duty Fairness", icon: "verified_user" },
  { href: "/exams/duties/rosters", label: "Duty Rosters", icon: "task_alt", detailLabel: "Roster" },
  { href: "/exams/duties/changes", label: "Swaps & Replacements", icon: "event_repeat" },
  { href: "/exams/duties/seating", label: "Student Seating", icon: "school" },
];
