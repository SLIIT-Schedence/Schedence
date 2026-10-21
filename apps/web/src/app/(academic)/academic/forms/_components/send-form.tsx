"use client";

import Link from "next/link";
import { useRef, useState, useTransition } from "react";
import { Badge, Card, EmptyState, PageHeader, formatDateTime } from "@/components/console/ui";
import type { ApiResult } from "@/lib/service-api";
import { formsHref, type DraftFormTemplate } from "@/lib/form-template-contracts";
import { reloadDraftForm } from "@/lib/form-template-actions";
import { refreshEligibleLecturers, sendFormDraft } from "@/lib/form-sending-actions";
import { audienceRecipients, departmentOptions, prepareSend, sendingError, sentFormsHref,
  type AudienceKind, type DistributionResult, type EligibleLecturer, type SendChannel } from "@/lib/form-sending-contracts";
import { CopyFormLink } from "./copy-form-link";
import { FormPreview } from "./form-preview";
import { CONTROL, LABEL, PRIMARY, SECONDARY } from "./styles";

export function SendForm({ initial, directory, institutionName }: { initial: DraftFormTemplate; directory: ApiResult<EligibleLecturer[]>; institutionName: string }) {
  const [template, setTemplate] = useState(initial);
  const [lecturers, setLecturers] = useState(directory.ok ? directory.data : []);
  const [directoryError, setDirectoryError] = useState(directory.ok ? "" : "Eligible lecturers could not be loaded. Retry before sending.");
  const [kind, setKind] = useState<AudienceKind>("SELECTED");
  const [selected, setSelected] = useState<string[]>([]);
  const [department, setDepartment] = useState("");
  const [channel, setChannel] = useState<SendChannel>("DIRECT");
  const [deadline, setDeadline] = useState("");
  const [search, setSearch] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [issued, setIssued] = useState<DistributionResult | null>(null);
  const [pending, startTransition] = useTransition();
  const request = useRef<{ fingerprint: string; key: string } | null>(null);
  const busy = useRef(false);
  const departments = departmentOptions(lecturers);
  const audience = audienceRecipients(lecturers, kind, selected, department);
  const blocked = audience.filter((row) => row.link_status !== "READY");
  const visible = lecturers.filter((row) => `${row.full_name} ${row.staff_number}`.toLowerCase().includes(search.trim().toLowerCase()));
  const missing = selected.filter((id) => !lecturers.some((row) => row.staff_id === id));

  function refresh() {
    startTransition(async () => {
      try {
        const result = await refreshEligibleLecturers(template.institution_id);
        if (!result.ok) { setDirectoryError("Eligible lecturers could not be loaded. Retry before sending."); return; }
        setLecturers(result.data); setDirectoryError("");
      } catch { setDirectoryError("Eligible lecturers could not be loaded. Retry before sending."); }
    });
  }
  function latest() {
    startTransition(async () => {
      try {
        const result = await reloadDraftForm(template.institution_id, template.id);
        if (!result.ok) { setNotice("The latest draft could not be loaded. Your selections are kept."); return; }
        setTemplate(result.data); setConflict(false); setNotice("Latest saved revision loaded. Review its questions before sending.");
      } catch { setNotice("The latest draft could not be loaded. Your selections are kept."); }
    });
  }
  function send(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy.current || issued || conflict || directoryError) return;
    const checked = prepareSend(template, lecturers, kind, selected, department, channel, deadline);
    setErrors(checked.errors); setNotice("");
    if (!checked.valid) return;
    const fingerprint = JSON.stringify(checked.body);
    if (request.current?.fingerprint !== fingerprint) request.current = { fingerprint, key: crypto.randomUUID() };
    const key = request.current.key;
    busy.current = true;
    startTransition(async () => {
      try {
        const result = await sendFormDraft(template.id, checked.body, key);
        if (!result.ok) { setNotice(sendingError(result.error)); setConflict(result.error.code === "REVISION_CONFLICT"); return; }
        setIssued(result.data);
      } catch { setNotice("Sending could not be confirmed. Retry with these selections to check the same request safely."); }
      finally { busy.current = false; }
    });
  }
  return <div className="space-y-6" aria-busy={pending}>
    <Link className="text-sm font-semibold text-muted hover:text-primary-strong" href={formsHref(template.institution_id, template.id)}>← Back to saved draft</Link>
    <PageHeader title="Send form" description={institutionName} actions={<Link className={SECONDARY} href={sentFormsHref(template.institution_id)}>Sent forms</Link>} />
    <Card title={template.title} description={`Saved revision ${template.revision} · ${template.questions.length} questions`}>
      <p className="px-5 py-4 text-sm text-muted">Sending freezes this revision and its recipient list. Later draft edits do not change a sent form.</p>
      <details className="border-t border-line"><summary className="cursor-pointer px-5 py-3 text-sm font-semibold text-primary-strong">Review saved questions</summary><div className="p-5"><FormPreview title={template.title} description={template.description} questions={template.questions} /></div></details>
    </Card>
    {issued ? <Card title="Form sent" description="The recipient list and form revision have been saved.">
      <div className="space-y-4 p-5"><Badge tone="accent">Sent</Badge><p className="text-sm text-ink">{issued.distribution.recipient_count} recipients · Deadline {formatDateTime(issued.distribution.deadline)} (Sri Lanka time)</p>
        <p className="text-sm text-muted">Linked recipients are pending access. This does not indicate an email or WhatsApp message was delivered.</p>
        {!!(issued.distribution.unlinked_count + issued.distribution.ambiguous_count) && <p role="status" className="text-sm text-danger">{issued.distribution.unlinked_count} need an account; {issued.distribution.ambiguous_count} need an account-link review. They cannot access this sent form.</p>}
        {issued.share_url && <CopyFormLink url={issued.share_url} />}
        <Link className={PRIMARY} href={sentFormsHref(template.institution_id, issued.distribution.id)}>View recipient status</Link>
      </div>
    </Card> : <form onSubmit={send} className="space-y-6">
      {notice && <div role="alert" className="rounded-xl border border-line bg-background p-4 text-sm text-ink"><p>{notice}</p>{conflict && <button type="button" disabled={pending} className={`mt-3 ${SECONDARY}`} onClick={latest}>Load latest saved draft</button>}</div>}
      {errors.form && <p role="alert" className="text-sm text-danger">{errors.form}</p>}
      <Card title="Recipients" actions={<button type="button" className={SECONDARY} onClick={refresh} disabled={pending}>Refresh eligibility</button>}>
        <fieldset disabled={pending} className="space-y-4 p-5"><legend className="sr-only">Recipient selection</legend>
          {directoryError && <p role="alert" className="text-sm text-danger">{directoryError}</p>}
          <label className={LABEL}>Send to<select className={CONTROL} value={kind} onChange={(event) => setKind(event.target.value as AudienceKind)}>
            <option value="SELECTED">Selected lecturers</option><option value="DEPARTMENT">A department</option><option value="ALL_ELIGIBLE">All eligible lecturers</option>
          </select></label>
          {kind === "DEPARTMENT" && <label className={LABEL}>Department<select className={CONTROL} value={department} onChange={(event) => setDepartment(event.target.value)}><option value="">Select a department</option>{departments.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>}
          {kind === "SELECTED" && <>
            <label className={LABEL}>Find lecturers<input type="search" className={CONTROL} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Name or staff number" /></label>
            <div className="flex flex-wrap items-center gap-3 text-sm"><span className="font-semibold text-ink">{selected.length} selected</span>
              <button type="button" className="font-semibold text-primary-strong hover:underline" onClick={() => setSelected((ids) => [...new Set([...ids, ...visible.map((row) => row.staff_id)])])}>Select all visible</button>
              <button type="button" className="font-semibold text-muted hover:underline" onClick={() => setSelected([])}>Clear selection</button>
            </div>
            <div className="max-h-80 divide-y divide-line overflow-y-auto rounded-xl border border-line" aria-label="Eligible lecturers">
              {!visible.length && <EmptyState icon="group" title="No matching lecturers">Refresh eligibility or change your search.</EmptyState>}
              {visible.map((row) => <label key={row.staff_id} className="flex cursor-pointer items-start gap-3 px-4 py-3 hover:bg-background">
                <input type="checkbox" className="mt-1 size-4 accent-primary" checked={selected.includes(row.staff_id)} onChange={(event) => setSelected((ids) => event.target.checked ? [...ids, row.staff_id] : ids.filter((id) => id !== row.staff_id))} />
                <span className="min-w-0 flex-1"><span className="block text-sm font-semibold text-ink">{row.full_name}</span><span className="text-xs text-muted">{row.staff_number}{row.departments?.length ? ` · ${row.departments.map((d) => d.name).join(", ")}` : ""}</span></span>
                <Badge tone={row.link_status === "READY" ? "neutral" : "danger"}>{row.link_status === "READY" ? "Linked account" : row.link_status === "NO_LINK" ? "Account required" : "Link needs review"}</Badge>
              </label>)}
            </div>
            {!!missing.length && <div role="alert" className="text-sm text-danger">{missing.length} selected lecturers are no longer eligible. <button type="button" className="font-semibold underline" onClick={() => setSelected((ids) => ids.filter((id) => !missing.includes(id)))}>Clear unavailable selections</button></div>}
          </>}
          <p className="text-sm text-muted">{audience.length} recipients · {audience.length - blocked.length} linked accounts</p>
          {!!blocked.length && <div role="status" className="rounded-xl border border-danger/20 bg-danger-soft p-4 text-sm text-danger">
            <p className="font-semibold">{blocked.length} recipients cannot access this form.</p><p>They have no linked account or an ambiguous account link. They remain tracked, but correcting a link will require a new send to grant access.</p>
            <p className="mt-2">{blocked.map((row) => `${row.full_name} (${row.staff_number})`).join(", ")}</p>
          </div>}
          {errors.audience && <p role="alert" className="text-sm text-danger">{errors.audience}</p>}
        </fieldset>
      </Card>
      <Card title="Delivery and deadline"><fieldset disabled={pending} className="space-y-4 p-5"><legend className="sr-only">Delivery and deadline</legend>
        <label className={LABEL}>Sending option<select className={CONTROL} value={channel} onChange={(event) => setChannel(event.target.value as SendChannel)}><option value="DIRECT">Assign to recipients</option><option value="SHARE_LINK">Create a shareable link</option></select></label>
        <p className="text-sm text-muted">{channel === "SHARE_LINK" ? "Copy the link for WhatsApp or email after sending. Forwarding it does not add recipients." : "Create assignments for the selected recipients. No email or WhatsApp message is sent automatically."}</p>
        <label className={LABEL}>Deadline (Sri Lanka time, UTC+05:30)<input type="datetime-local" required className={CONTROL} value={deadline} onChange={(event) => setDeadline(event.target.value)} aria-invalid={!!errors.deadline} /></label>
        {errors.deadline && <p role="alert" className="text-sm text-danger">{errors.deadline}</p>}
      </fieldset></Card>
      <div className="flex flex-wrap items-center gap-3"><button type="submit" className={PRIMARY} disabled={pending || !!directoryError || conflict || !template.questions.length}>{pending ? "Sending…" : channel === "SHARE_LINK" ? "Send and create shareable link" : "Send form"}</button><span className="text-sm text-muted">Deadline and recipients are fixed after sending.</span></div>
    </form>}
    <p className="text-sm text-muted">Lecturer response pages and submissions are not available yet. Sent forms track account eligibility and access only.</p>
  </div>;
}
