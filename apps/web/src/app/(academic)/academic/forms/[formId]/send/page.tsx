import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { Suspense } from "react";
import { Card, first, LoadError, TableSkeleton } from "@/components/console/ui";
import { getAcademicContext } from "@/lib/academic";
import { getFormTemplate } from "@/lib/form-templates";
import { getFormRecipients } from "@/lib/form-sending";
import { sendingHref } from "@/lib/form-sending-contracts";
import { isUuid } from "@/lib/identity-records";
import { SendForm } from "../../_components/send-form";

export const metadata: Metadata = { title: "Send form" };
type Props = { params: Promise<{ formId: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> };
export default function SendFormPage(props: Props) {
  return <Suspense fallback={<Card><TableSkeleton rows={6} /></Card>}><Content {...props} /></Suspense>;
}
async function Content({ params, searchParams }: Props) {
  const context = await getAcademicContext();
  if (!context) redirect("/");
  const { formId } = await params;
  const requested = first((await searchParams).institution);
  const institution = requested ? context.institutions.find((row) => row.id === requested) : context.primary;
  if (!institution || institution.status !== "ACTIVE" || !isUuid(formId)) notFound();
  const [result, directory] = await Promise.all([getFormTemplate(institution.id, formId), getFormRecipients(institution.id)]);
  if (!result.ok) {
    if (result.error.status === 404) notFound();
    return <Card><LoadError error={result.error} what="this saved draft" retryHref={sendingHref(institution.id, formId)} service="Teaching" /></Card>;
  }
  return <SendForm key={`${institution.id}:${formId}`} initial={result.data} directory={directory} institutionName={institution.name} />;
}
