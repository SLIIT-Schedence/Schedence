import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { Badge, Card, EmptyState, PageHeader, PageSkeleton } from "@/components/console/ui";
import { requireExaminationAdmin } from "@/lib/examination-access";
import { EXAM_NAV } from "../_navigation";

export const metadata = { title: "Examination workspace" };

export default function ComingSoonPage({ params }: { params: Promise<{ section: string }> }) {
  return <Suspense fallback={<PageSkeleton />}><ComingSoon params={params} /></Suspense>;
}

async function ComingSoon({ params }: { params: Promise<{ section: string }> }) {
  await requireExaminationAdmin();
  const { section } = await params;
  const item = EXAM_NAV.find((entry) => entry.href === `/exams/${section}`);
  if (!item) notFound();
  return (
    <div className="space-y-6">
      <PageHeader title={item.label} description="Your examination workspace is growing." actions={<Badge tone="neutral">Coming Soon</Badge>} />
      <Card>
        <EmptyState icon={item.icon} title={`${item.label} — Coming Soon`} action={<Link href="/exams" className="inline-flex rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-primary-strong">Back to overview</Link>}>
          This section will be available in a future release. You can view current examination data on the overview.
        </EmptyState>
      </Card>
    </div>
  );
}
