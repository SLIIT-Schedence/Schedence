import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { Suspense } from "react";
import { Card, first, LoadError, PageHeader, TableSkeleton } from "@/components/console/ui";
import { getAcademicContext } from "@/lib/academic";
import { formsHref } from "@/lib/form-template-contracts";
import { getFormPresets } from "@/lib/form-templates";
import { CreateForm } from "../_components/create-form";

export const metadata: Metadata = { title: "Create form" };
type SearchParams = Promise<Record<string, string | string[] | undefined>>;
export default function NewFormPage({ searchParams }: { searchParams: SearchParams }) {
  return <Suspense fallback={<Card><TableSkeleton rows={5} /></Card>}><NewForm searchParams={searchParams} /></Suspense>;
}
async function NewForm({ searchParams }: { searchParams: SearchParams }) {
  const context = await getAcademicContext();
  if (!context) redirect("/");
  const requested = first((await searchParams).institution);
  const institution = requested ? context.institutions.find((row) => row.id === requested) : context.primary;
  if (!institution || institution.status !== "ACTIVE") notFound();
  const result = await getFormPresets(institution.id);
  return <div className="space-y-6">
    <Link href={formsHref(institution.id)} className="text-sm font-semibold text-muted hover:text-primary-strong">← All forms</Link>
    <PageHeader title="Create form" description={`Choose a starting point for ${institution.name}.`} />
    {!result.ok ? <Card><LoadError error={result.error} what="form templates" service="Teaching" retryHref={formsHref(institution.id, "new")} /></Card> :
      <CreateForm institution={institution.id} presets={result.data} />}
  </div>;
}
