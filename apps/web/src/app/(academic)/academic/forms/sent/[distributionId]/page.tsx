import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { Suspense } from "react";
import { Badge, Card, EmptyState, first, formatDateTime, LoadError, PageHeader, pageNumber, Pagination, TableSkeleton } from "@/components/console/ui";
import { getAcademicContext } from "@/lib/academic";
import { getDistribution, getDistributionRecipients } from "@/lib/form-sending";
import { recipientStatus, sentFormsHref } from "@/lib/form-sending-contracts";
import { isUuid } from "@/lib/identity-records";
import { CopyFormLink } from "../../_components/copy-form-link";
import { SECONDARY } from "../../_components/styles";

export const metadata: Metadata = { title: "Sent form recipients" };
type Props = { params: Promise<{ distributionId: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> };
export default function SentFormPage(props: Props) {
  return <Suspense fallback={<Card><TableSkeleton rows={6} /></Card>}><Content {...props} /></Suspense>;
}
async function Content({ params, searchParams }: Props) {
  const context = await getAcademicContext();
  if (!context) redirect("/");
  const { distributionId } = await params;
  const search = await searchParams;
  const requested = first(search.institution);
  const institution = requested ? context.institutions.find((row) => row.id === requested) : context.primary;
  if (!institution || institution.status !== "ACTIVE" || !isUuid(distributionId)) notFound();
  const [result, recipients] = await Promise.all([getDistribution(institution.id, distributionId), getDistributionRecipients(institution.id, distributionId, pageNumber(search.page))]);
  if (!result.ok) {
    if (result.error.status === 404) notFound();
    return <Card><LoadError error={result.error} what="this sent form" retryHref={sentFormsHref(institution.id, distributionId)} service="Teaching" /></Card>;
  }
  const { distribution: row, status, share_url } = result.data;
  return <div className="space-y-6"><PageHeader title={row.frozen_template.title} description={institution.name} actions={<Link className={SECONDARY} href={sentFormsHref(institution.id)}>All sent forms</Link>} />
    <Card title="Sent form"><div className="space-y-4 p-5">
      <div className="flex flex-wrap gap-2"><Badge tone="accent">Sent</Badge><Badge tone={status === "OPEN" ? "primary" : "neutral"}>{status === "OPEN" ? "Open" : "Closed"}</Badge></div>
      <dl className="grid gap-4 text-sm sm:grid-cols-2"><div><dt className="text-muted">Frozen revision</dt><dd className="mt-1 font-semibold text-ink">{row.frozen_template.revision}</dd></div><div><dt className="text-muted">Deadline (Sri Lanka time)</dt><dd className="mt-1 font-semibold text-ink">{formatDateTime(row.deadline)}</dd></div><div><dt className="text-muted">Recipients</dt><dd className="mt-1 font-semibold text-ink">{row.recipient_count}</dd></div><div><dt className="text-muted">Sent (Sri Lanka time)</dt><dd className="mt-1 font-semibold text-ink">{formatDateTime(row.created_at)}</dd></div></dl>
      {!!(row.unlinked_count + row.ambiguous_count) && <p className="rounded-xl bg-danger-soft p-3 text-sm text-danger">{row.unlinked_count} recipients need an account; {row.ambiguous_count} need an account-link review. They cannot access this sent form.</p>}
      {share_url && <CopyFormLink key={share_url} url={share_url} />}
      <p className="text-sm text-muted">Lecturer response pages and submissions are not available yet. Links require a signed-in, authorized recipient.</p>
    </div></Card>
    <Card title="Recipient tracking" description="Pending access means the recipient has not acknowledged opening this form.">
      {!recipients.ok ? <LoadError error={recipients.error} what="recipient status" retryHref={sentFormsHref(institution.id, distributionId)} service="Teaching" /> : !recipients.data.total ? <EmptyState icon="group" title="No recipients returned">Refresh this page to check the saved recipient list.</EmptyState> : <>
        <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-line bg-background/60 text-xs text-muted"><tr><th scope="col" className="px-5 py-3">Lecturer</th><th scope="col" className="px-5 py-3">Status</th><th scope="col" className="px-5 py-3">First opened (Sri Lanka)</th></tr></thead>
          <tbody className="divide-y divide-line">{recipients.data.items.map((recipient) => <tr key={recipient.staff_id} className="hover:bg-background/50"><td className="px-5 py-4"><p className="font-semibold text-ink">{recipient.full_name}</p><p className="mt-1 text-xs text-muted">{recipient.staff_number}</p></td><td className="px-5 py-4"><Badge tone={recipient.link_status !== "READY" ? "danger" : recipient.first_opened_at ? "accent" : "neutral"}>{recipientStatus(recipient)}</Badge></td><td className="px-5 py-4 text-muted">{recipient.first_opened_at ? formatDateTime(recipient.first_opened_at) : "—"}</td></tr>)}</tbody>
        </table></div><Pagination page={recipients.data.page} pageSize={recipients.data.page_size} total={recipients.data.total} hrefFor={(page) => sentFormsHref(institution.id, distributionId, page)} />
      </>}
    </Card>
  </div>;
}
