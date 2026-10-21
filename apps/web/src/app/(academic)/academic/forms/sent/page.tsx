import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { Suspense } from "react";
import { Badge, Card, EmptyState, first, formatDateTime, LoadError, PageHeader, pageNumber, Pagination, TableSkeleton } from "@/components/console/ui";
import { getAcademicContext } from "@/lib/academic";
import { formsHref } from "@/lib/form-template-contracts";
import { getDistributions } from "@/lib/form-sending";
import { sentFormsHref } from "@/lib/form-sending-contracts";
import { SECONDARY } from "../_components/styles";

export const metadata: Metadata = { title: "Sent forms" };
type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };
export default function SentFormsPage(props: Props) {
  return <Suspense fallback={<Card><TableSkeleton rows={5} /></Card>}><Content {...props} /></Suspense>;
}
async function Content({ searchParams }: Props) {
  const context = await getAcademicContext();
  if (!context) redirect("/");
  const params = await searchParams;
  const requested = first(params.institution);
  const institution = requested ? context.institutions.find((row) => row.id === requested) : context.primary;
  if (!institution || institution.status !== "ACTIVE") notFound();
  const result = await getDistributions(institution.id, pageNumber(params.page));
  return <div className="space-y-6"><PageHeader title="Sent forms" description={institution.name} actions={<Link className={SECONDARY} href={formsHref(institution.id)}>Draft forms</Link>} />
    <Card title="Sent forms" description="Each send preserves its own form revision, recipients and deadline.">
      {!result.ok ? <LoadError error={result.error} what="sent forms" retryHref={sentFormsHref(institution.id)} service="Teaching" /> : !result.data.total ? <EmptyState icon="edit_note" title="No sent forms yet">Open a saved draft and choose Send form.</EmptyState> : <>
        <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-line bg-background/60 text-xs text-muted"><tr>
          <th scope="col" className="px-5 py-3">Form</th><th scope="col" className="px-5 py-3">Recipients</th><th scope="col" className="px-5 py-3">Deadline (Sri Lanka)</th><th scope="col" className="px-5 py-3">Status</th>
        </tr></thead><tbody className="divide-y divide-line">{result.data.items.map(({ distribution: row, status }) => <tr key={row.id} className="hover:bg-background/50">
          <td className="px-5 py-4"><Link className="font-semibold text-primary-strong hover:underline" href={sentFormsHref(institution.id, row.id)}>{row.frozen_template.title}</Link><p className="mt-1 text-xs text-muted">Revision {row.frozen_template.revision} · {row.channel === "SHARE_LINK" ? "Shareable link" : "Recipient assignments"} · Sent {formatDateTime(row.created_at)}</p></td>
          <td className="px-5 py-4"><p>{row.recipient_count} recipients</p>{!!(row.unlinked_count + row.ambiguous_count) && <p className="mt-1 text-xs text-danger">{row.unlinked_count + row.ambiguous_count} account issues</p>}</td>
          <td className="px-5 py-4 text-muted">{formatDateTime(row.deadline)}</td><td className="px-5 py-4"><div className="flex flex-wrap gap-2"><Badge tone="accent">Sent</Badge><Badge tone={status === "OPEN" ? "primary" : "neutral"}>{status === "OPEN" ? "Open" : "Closed"}</Badge></div></td>
        </tr>)}</tbody></table></div><Pagination page={result.data.page} pageSize={result.data.page_size} total={result.data.total} hrefFor={(page) => sentFormsHref(institution.id, undefined, page)} />
      </>}
    </Card><p className="text-sm text-muted">Sent confirms an assignment was saved. Recipient details show whether access is pending or opened; submissions are not available yet.</p>
  </div>;
}
