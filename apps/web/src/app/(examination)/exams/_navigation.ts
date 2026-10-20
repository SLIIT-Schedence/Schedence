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
];
