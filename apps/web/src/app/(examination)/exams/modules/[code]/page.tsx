import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { Badge, Card, PageHeader, PageSkeleton } from "@/components/console/ui";
import { requireExaminationAdmin } from "@/lib/examination-access";
import { getExamModule } from "@/lib/examination";
import { missingModuleFields, MODULE_COLUMNS } from "@/lib/examination-module-catalog";
import { ExaminationReadError } from "../../_components/read-error";
import { RefreshButton } from "../../_components/refresh-button";
import { ModuleValue, RecordedBoolean, RecordedMinutes, RecordedText } from "../_components/module-values";

export const metadata = { title: "Module details" };
type Params = Promise<{ code: string }>;

export default function ModuleDetailsPage({ params }: { params: Params }) {
  return <div className="space-y-6">
    <Link href="/exams/modules" className="text-sm font-semibold text-primary-strong hover:underline">Back to modules</Link>
    <Suspense fallback={<PageSkeleton />}><ModuleDetails params={params} /></Suspense>
  </div>;
}

async function ModuleDetails({ params }: { params: Params }) {
  await requireExaminationAdmin();
  const { code } = await params;
  if (!/^[A-Z]{2,4}[0-9]{4}$/.test(code)) notFound();
  const result = await getExamModule(code);
  if (!result.ok) {
    if (result.error.status === 404) notFound();
    return <Card><ExaminationReadError error={result.error} what="this module" /></Card>;
  }
  const entry = result.data;
  const missing = missingModuleFields(entry);
  const details = [
    ...Object.entries(MODULE_COLUMNS).map(([key, label]) => [label, <ModuleValue key={key} module={entry} field={key as keyof typeof MODULE_COLUMNS} />] as const),
    ["Exam type", <RecordedText key="exam_type" value={entry.exam_type} />],
    ["FINAL eligible", <RecordedBoolean key="has_final_exam" value={entry.has_final_exam} />],
    ["MID eligible", <RecordedBoolean key="has_mid_exam" value={entry.has_mid_exam} />],
    ["MID duration", entry.has_mid_exam === false ? "No MID exam" : <RecordedMinutes key="mid_duration" value={entry.mid_duration_minutes} />],
    ["Extra reading time", <RecordedBoolean key="reading" value={entry.has_extra_reading_time} />],
    ["Reading time", <RecordedMinutes key="reading_time" value={entry.reading_time_minutes} />],
    ["Delivery mode", <RecordedText key="delivery" value={entry.delivery_mode} />],
    ["Block category", entry.delivery_mode === "regular" ? "Not applicable" : <RecordedText key="block" value={entry.block_category} />],
  ];
  return <>
    <PageHeader title={entry.module_code} description={entry.module_name?.trim() || "Module name not available"}
      actions={<><Badge tone="neutral">Read only</Badge><RefreshButton label="Refresh" /></>} />
    {missing.length > 0 && <p className="rounded-xl bg-accent p-4 text-sm text-accent-ink">Missing data: {missing.join(", ")}. Values have not been inferred.</p>}
    <Card title="Module details" description="Recorded examination data for this existing module.">
      <dl className="grid sm:grid-cols-2 lg:grid-cols-3">{details.map(([label, value]) => <div key={String(label)} className="border-b border-line px-5 py-4">
        <dt className="text-xs font-semibold text-muted">{label}</dt><dd className="mt-2 text-sm text-ink">{value}</dd>
      </div>)}</dl>
      <p className="p-5 text-xs text-muted">Registrations are the total recorded for this module, across its specializations. No normal/repeat breakdown is returned by this module endpoint.</p>
    </Card>
  </>;
}
