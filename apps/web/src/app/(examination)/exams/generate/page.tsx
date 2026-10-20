import type { Metadata } from "next";
import { Suspense } from "react";
import { Card, EmptyState, PageHeader, PageSkeleton } from "@/components/console/ui";
import { requireExaminationAdmin } from "@/lib/examination-access";
import { listExamModules, listExamPeriodModules, listExamPeriods } from "@/lib/examination";
import { cycleRegistrations, type ExamOption } from "@/lib/examination-selection";
import { ExaminationReadError } from "../_components/read-error";
import { RefreshButton } from "../_components/refresh-button";
import { GenerationModes } from "./_components/generation-modes";

export const metadata: Metadata = { title: "Generate FINAL timetable" };

export default async function GeneratePage({ searchParams }: { searchParams: Promise<{ semesters?: string }> }) {
  const both = (await searchParams).semesters === "both";
  return (
    <div className="space-y-6">
      <PageHeader title="Generate FINAL timetable" description="Select the exam, choose dates, review modules, then generate and explore a full-cycle timetable. Optimized mode is recommended." actions={<RefreshButton label="Refresh examinations" />} />
      <Suspense fallback={<PageSkeleton />}>
        <GenerationWorkspace both={both} />
      </Suspense>
    </div>
  );
}

async function GenerationWorkspace({ both }: { both: boolean }) {
  await requireExaminationAdmin();
  const [periods, modules] = await Promise.all([listExamPeriods(), listExamModules()]);
  if (!periods.ok) return <Card><ExaminationReadError error={periods.error} what="examination periods" /></Card>;
  // Every configured period (MID and FINAL), with counts only where the real mapping gives them exactly.
  const options: ExamOption[] = await Promise.all(periods.data.map(async (period) => {
    const mapping = await listExamPeriodModules(period.period_code);
    return {
      period_code: period.period_code, period_name: period.period_name, academic_year_label: period.academic_year_label,
      semester: period.semester, exam_kind: period.exam_kind, start_date: period.start_date, end_date: period.end_date,
      exam_days: period.exam_days.length,
      module_count: mapping.ok ? mapping.data.length : null,
      registration_count: mapping.ok && modules.ok ? cycleRegistrations(mapping.data, modules.data) : null,
    };
  }));
  if (!options.length) {
    return <Card><EmptyState icon="history" title="No examination periods configured">An examination period must be configured in the Examination Service before you can generate a timetable.</EmptyState></Card>;
  }
  return <GenerationModes cycles={options} initialBoth={both} />;
}
