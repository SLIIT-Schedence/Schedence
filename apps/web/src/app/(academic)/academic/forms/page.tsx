import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { Suspense } from "react";
import { Badge, Card, EmptyState, first, formatDateTime, LoadError, PageHeader, pageNumber, Pagination, query, TableSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { getAcademicContext } from "@/lib/academic";
import { formsHref } from "@/lib/form-template-contracts";
import { listFormTemplates } from "@/lib/form-templates";
import { sendingHref, sentFormsHref } from "@/lib/form-sending-contracts";

export const metadata: Metadata = { title: "Forms" };
type SearchParams = Promise<Record<string, string | string[] | undefined>>;
const PRIMARY = "inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white hover:bg-primary-strong";

export default function FormsPage({ searchParams }: { searchParams: SearchParams }) {
  return <Suspense fallback={<Card><TableSkeleton rows={5} /></Card>}><Forms searchParams={searchParams} /></Suspense>;
}

async function Forms({ searchParams }: { searchParams: SearchParams }) {
  const context = await getAcademicContext();
  if (!context) redirect("/");
  const params = await searchParams;
  const requested = first(params.institution);
  const institution = requested ? context.institutions.find((row) => row.id === requested) : context.primary;
  if (!institution) notFound();
  const page = pageNumber(params.page);
  const result = await listFormTemplates(institution.id, page);
  const newHref = formsHref(institution.id, "new");
  return <div className="space-y-6">
    <PageHeader title="Forms" description={`Build and save draft questionnaires for ${institution.name}.`} actions={<><Link href={sentFormsHref(institution.id)} className="inline-flex h-10 items-center rounded-xl border border-line px-4 text-sm font-semibold text-ink hover:bg-background">Sent forms</Link><Link href={newHref} className={PRIMARY}><Icon name="edit_note" size={18} />Create form</Link></>} />
    {context.institutions.length > 1 && <form action="/academic/forms" className="flex flex-wrap items-end gap-3">
      <label className="text-[13px] font-semibold text-muted">Institution<select name="institution" defaultValue={institution.id} className="mt-1 block h-10 rounded-xl border border-line bg-surface px-3 text-sm text-ink">
        {context.institutions.map((row) => <option key={row.id} value={row.id} disabled={row.status !== "ACTIVE"}>{row.name}</option>)}
      </select></label><button className={PRIMARY}>View forms</button>
    </form>}
    <Card title="Draft forms" description="Start with a blank form or the Lecturer Teaching Preferences template.">
      {!result.ok ? <LoadError error={result.error} what="draft forms" retryHref={formsHref(institution.id)} service="Teaching" /> : result.data.total === 0 ?
        <EmptyState icon="edit_note" title="No draft forms yet" action={<Link href={newHref} className={PRIMARY}>Create your first form</Link>}>Choose a template or add your own questions.</EmptyState> : <>
          <div className="overflow-x-auto"><table className="w-full text-left text-sm">
            <thead className="border-b border-line bg-background/60 text-xs text-muted"><tr><th scope="col" className="px-5 py-3">Form</th><th scope="col" className="px-5 py-3">Questions</th><th scope="col" className="px-5 py-3">Last saved</th><th scope="col" className="px-5 py-3">Status</th></tr></thead>
            <tbody className="divide-y divide-line">{result.data.items.map((form) => <tr key={form.id} className="hover:bg-background/50">
              <td className="px-5 py-4"><Link href={formsHref(institution.id, form.id)} className="font-semibold text-primary-strong hover:underline">{form.title}</Link><p className="mt-1 text-xs text-muted">{form.preset_id ? "Lecturer preferences" : "Custom form"} · Revision {form.revision}</p></td>
              <td className="px-5 py-4 tabular-nums">{form.questions.length}</td><td className="px-5 py-4 text-muted">{formatDateTime(form.updated_at)}</td><td className="px-5 py-4"><div className="flex flex-wrap items-center gap-3"><Badge tone="neutral">Draft</Badge>{form.questions.length > 0 && <Link href={sendingHref(institution.id, form.id)} className="font-semibold text-primary-strong hover:underline">Send form</Link>}</div></td>
            </tr>)}</tbody>
          </table></div>
          <Pagination page={result.data.page} pageSize={result.data.page_size} total={result.data.total} hrefFor={(next) => `/academic/forms${query({ institution: institution.id, page: next })}`} />
        </>}
    </Card>
  </div>;
}
