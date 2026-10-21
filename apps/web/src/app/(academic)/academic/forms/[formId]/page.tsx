import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { Suspense } from "react";
import { Card, first, LoadError, TableSkeleton } from "@/components/console/ui";
import { getAcademicContext } from "@/lib/academic";
import { formsHref } from "@/lib/form-template-contracts";
import { getFormTemplate } from "@/lib/form-templates";
import { isUuid } from "@/lib/identity-records";
import { FormEditor } from "../_components/form-editor";

export const metadata: Metadata = { title: "Form builder" };
type Props = { params: Promise<{ formId: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> };
export default function FormBuilderPage(props: Props) {
  return <Suspense fallback={<Card><TableSkeleton rows={6} /></Card>}><Builder {...props} /></Suspense>;
}
async function Builder({ params, searchParams }: Props) {
  const context = await getAcademicContext();
  if (!context) redirect("/");
  const { formId } = await params;
  const requested = first((await searchParams).institution);
  const institution = requested ? context.institutions.find((row) => row.id === requested) : context.primary;
  if (!institution || institution.status !== "ACTIVE" || !isUuid(formId)) notFound();
  const result = await getFormTemplate(institution.id, formId);
  if (!result.ok) {
    if (result.error.status === 404) notFound();
    return <div className="space-y-6"><Link href={formsHref(institution.id)} className="text-sm font-semibold text-muted">← All forms</Link><Card><LoadError error={result.error} what="this draft form" retryHref={formsHref(institution.id, formId)} service="Teaching" /></Card></div>;
  }
  return <FormEditor key={`${institution.id}:${formId}`} initial={result.data} institutionName={institution.name} />;
}
