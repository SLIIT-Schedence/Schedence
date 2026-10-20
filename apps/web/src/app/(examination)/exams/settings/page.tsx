import { Suspense } from "react";
import { PageHeader, PageSkeleton } from "@/components/console/ui";
import { requireExaminationAdmin } from "@/lib/examination-access";
import { examinationRequest } from "@/lib/examination-api";
import { schedulingReview, seatingReview } from "@/lib/examination-settings";
import type { SchedulingReview, SeatingReview } from "@/lib/examination-workspace-types";
import { ExaminationReadError } from "../_components/read-error";
import { InstitutionSettings } from "./_components/institution-settings";

export const metadata = { title: "Examination settings" };

export default function SettingsPage() {
  return <div className="space-y-6"><PageHeader title="Institution examination defaults" description="Manage shared defaults here. Customize individual timetables in the Generate wizard." /><Suspense fallback={<PageSkeleton />}><Settings /></Suspense></div>;
}

async function Settings() {
  await requireExaminationAdmin();
  const [scheduling, seating] = await Promise.all([
    examinationRequest<SchedulingReview>("/api/v1/settings/scheduling", { query: { history_limit: 0 } }),
    examinationRequest<SeatingReview>("/api/v1/settings/exam-seating", { query: { history_limit: 0 } }),
  ]);
  if (!scheduling.ok) return <ExaminationReadError error={scheduling.error} what="institution scheduling defaults" />;
  if (!seating.ok) return <ExaminationReadError error={seating.error} what="institution seating defaults" />;
  return <InstitutionSettings scheduling={schedulingReview(scheduling.data)} seating={seatingReview(seating.data)} />;
}
