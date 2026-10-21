"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { Badge, Card, EmptyState, PageHeader } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { duplicateDraftForm, reloadDraftForm, saveDraftForm } from "@/lib/form-template-actions";
import { changeFieldType, CUSTOM_TYPES, customQuestion, FIELD_LABELS, formErrorMessage, formsHref, moveQuestion, questionDraft, validateDraft,
  type CustomFieldType, type DraftFormTemplate, type FormQuestion, type QuestionDraft } from "@/lib/form-template-contracts";
import { FormPreview } from "./form-preview";
import { sendingHref } from "@/lib/form-sending-contracts";
import { CONTROL, LABEL, PRIMARY, SECONDARY } from "./styles";

type Notice = { error: boolean; message: string } | null;

export function FormEditor({ initial, institutionName }: { initial: DraftFormTemplate; institutionName: string }) {
  const router = useRouter();
  const [saved, setSaved] = useState(initial);
  const [title, setTitle] = useState(initial.title);
  const [description, setDescription] = useState(initial.description);
  const [questions, setQuestions] = useState(() => initial.questions.map(questionDraft));
  const [preview, setPreview] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [conflict, setConflict] = useState(false);
  const [pending, startTransition] = useTransition();
  const dirty = JSON.stringify({ title, description, questions }) !== JSON.stringify({ title: saved.title, description: saved.description, questions: saved.questions.map(questionDraft) });
  const checked = validateDraft(title, description, questions, saved.questions);
  useEffect(() => {
    if (!dirty) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  function applySaved(form: DraftFormTemplate) {
    setSaved(form); setTitle(form.title); setDescription(form.description); setQuestions(form.questions.map(questionDraft)); setErrors({}); setConflict(false);
  }
  function updateQuestion(id: string, update: (row: QuestionDraft) => QuestionDraft) {
    setQuestions((rows) => rows.map((row) => row.question.id === id ? update(row) : row));
    setNotice(null);
  }
  function save() {
    setErrors(checked.errors);
    if (!checked.valid) { setNotice({ error: true, message: "Review the highlighted fields before saving." }); setPreview(false); return; }
    setNotice(null);
    startTransition(async () => {
      try {
        const result = await saveDraftForm(saved.id, { institution_id: saved.institution_id, expected_revision: saved.revision,
          title: title.trim(), description, questions: checked.questions });
        if (!result.ok) { setNotice({ error: true, message: formErrorMessage(result.error) }); setConflict(result.error.status === 409); return; }
        applySaved(result.data); setNotice({ error: false, message: "Draft saved." });
      } catch { setNotice({ error: true, message: "The draft could not be saved. Your edits are kept; try again." }); }
    });
  }
  function duplicate() {
    startTransition(async () => {
      try {
        const result = await duplicateDraftForm(saved.institution_id, saved.id);
        if (!result.ok) { setNotice({ error: true, message: formErrorMessage(result.error) }); return; }
        router.push(formsHref(saved.institution_id, result.data.id));
      } catch { setNotice({ error: true, message: "The saved draft could not be duplicated. Try again." }); }
    });
  }
  function reload() {
    if (dirty && !window.confirm("Replace your unsaved edits with the latest saved draft?")) return;
    startTransition(async () => {
      try {
        const result = await reloadDraftForm(saved.institution_id, saved.id);
        if (!result.ok) { setNotice({ error: true, message: formErrorMessage(result.error) }); return; }
        applySaved(result.data); setNotice({ error: false, message: "Latest saved draft loaded." });
      } catch { setNotice({ error: true, message: "The latest draft could not be loaded. Your edits are kept." }); }
    });
  }
  return <div className="space-y-6" aria-busy={pending}>
    <Link href={formsHref(saved.institution_id)} onClick={(event) => { if (dirty && !window.confirm("Leave without saving your changes?")) event.preventDefault(); }} className="text-sm font-semibold text-muted hover:text-primary-strong">← All forms</Link>
    <PageHeader title="Form builder" description={institutionName} actions={<>
      {pending || dirty || conflict || !saved.questions.length ? <button type="button" className={SECONDARY} disabled title="Save a draft with questions before sending">Send form</button> : <Link className={SECONDARY} href={sendingHref(saved.institution_id, saved.id)}>Send form</Link>}
      <button type="button" className={SECONDARY} disabled={pending || dirty} onClick={duplicate} title={dirty ? "Save changes before duplicating" : "Duplicate the saved draft"}>Duplicate</button>
      <button type="button" className={PRIMARY} disabled={pending || conflict} onClick={save}><Icon name="save" size={18} />{pending ? "Working…" : "Save draft"}</button>
    </>} />
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-2"><Badge tone="neutral">Draft</Badge><span className="text-[13px] text-muted">Revision {saved.revision} · {dirty ? "Unsaved changes" : "All changes saved"}</span>{dirty && <span className="text-[13px] text-muted">Save before duplicating.</span>}</div>
      <div className="inline-flex rounded-xl border border-line bg-surface p-1" role="group" aria-label="Builder view">
        <button type="button" aria-pressed={!preview} onClick={() => setPreview(false)} className={`rounded-lg px-4 py-2 text-sm font-semibold ${!preview ? "bg-primary-soft text-primary-strong" : "text-muted"}`}>Edit questions</button>
        <button type="button" aria-pressed={preview} onClick={() => setPreview(true)} className={`rounded-lg px-4 py-2 text-sm font-semibold ${preview ? "bg-primary-soft text-primary-strong" : "text-muted"}`}>Preview</button>
      </div>
    </div>
    {(notice || conflict) && <div role={notice?.error || conflict ? "alert" : "status"} className={`rounded-xl border px-4 py-3 text-sm ${notice?.error || conflict ? "border-danger/20 bg-danger-soft text-danger" : "border-line bg-primary-soft text-primary-strong"}`}>
      <p>{notice?.message ?? "A newer draft is available. Your edits are kept. Reload the latest draft before saving again."}</p>{conflict && <button type="button" className={`mt-3 ${SECONDARY}`} disabled={pending} onClick={reload}>Reload latest draft</button>}
    </div>}
    {preview ? <FormPreview title={title} description={description} questions={checked.questions} /> : <>
      <Card title="Form details"><fieldset disabled={pending} className="space-y-4 p-5">
        <label className={LABEL}>Form title<input value={title} onChange={(event) => { setTitle(event.target.value); setNotice(null); }} className={CONTROL} maxLength={200} aria-invalid={!!errors.title} aria-describedby={errors.title ? "title-error" : undefined} /></label>
        {errors.title && <p id="title-error" className="text-sm text-danger">{errors.title}</p>}
        <label className={LABEL}>Description <span className="font-normal">(optional)</span><textarea value={description} onChange={(event) => { setDescription(event.target.value); setNotice(null); }} className={CONTROL} rows={3} maxLength={4000} aria-invalid={!!errors.description} /></label>
        {errors.description && <p className="text-sm text-danger">{errors.description}</p>}
      </fieldset></Card>
      <section aria-label="Questions" className="space-y-4">
        <div className="flex items-center justify-between gap-3"><h2 className="text-base font-semibold text-ink">Questions <span className="font-normal text-muted">({questions.length}/100)</span></h2>
          <button type="button" className={SECONDARY} disabled={pending || questions.length >= 100} onClick={() => { setQuestions((rows) => [...rows, customQuestion()]); setNotice(null); }}>Add question</button>
        </div>
        {errors.questions && <p role="alert" className="text-sm text-danger">{errors.questions}</p>}
        {!questions.length && <Card><EmptyState icon="edit_note" title="Start with your first question">Add a question, then choose its format and wording.</EmptyState></Card>}
        {questions.map((row, index) => <QuestionEditor key={row.question.id} draft={row} index={index} count={questions.length} pending={pending} error={errors[row.question.id]}
          update={(fn) => updateQuestion(row.question.id, fn)}
          move={(direction) => { setQuestions((rows) => moveQuestion(rows, index, direction)); setNotice(null); }}
          remove={() => { setQuestions((rows) => rows.filter((item) => item.question.id !== row.question.id)); setNotice(null); }} />)}
      </section>
    </>}
  </div>;
}

function QuestionEditor({ draft, index, count, pending, error, update, move, remove }: {
  draft: QuestionDraft; index: number; count: number; pending: boolean; error?: string;
  update: (fn: (row: QuestionDraft) => QuestionDraft) => void; move: (direction: -1 | 1) => void; remove: () => void;
}) {
  const q = draft.question;
  const locked = q.kind === "LOCKED";
  const patch = (value: Partial<FormQuestion>) => update((row) => ({ ...row, question: { ...row.question, ...value } }));
  return <Card><fieldset disabled={pending} className="min-w-0 space-y-4 p-5">
    <legend className="sr-only">Question {index + 1}</legend>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-2"><span className="text-sm font-semibold text-ink">Question {index + 1}</span><Badge tone={locked ? "primary" : "neutral"}>{locked ? "Locked format" : q.kind === "SUGGESTED" ? "Suggested" : "Custom"}</Badge></div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={SECONDARY} disabled={pending || index === 0} onClick={() => move(-1)} aria-label={`Move question ${index + 1} up`}>↑<span className="sr-only">Move up</span></button>
        <button type="button" className={SECONDARY} disabled={pending || index === count - 1} onClick={() => move(1)} aria-label={`Move question ${index + 1} down`}>↓<span className="sr-only">Move down</span></button>
        {!locked && <button type="button" className={SECONDARY} onClick={remove} aria-label={`Remove question ${index + 1}`}><Icon name="close" size={18} />Remove</button>}
      </div>
    </div>
    <label className={LABEL}>Question wording<input className={CONTROL} value={q.label} onChange={(event) => patch({ label: event.target.value })} maxLength={200} aria-invalid={!!error} aria-describedby={error ? `error-${q.id}` : undefined} /></label>
    <label className={LABEL}>Guidance <span className="font-normal">(optional)</span><textarea className={CONTROL} rows={2} value={q.help_text} onChange={(event) => patch({ help_text: event.target.value })} maxLength={4000} /></label>
    {locked ? <div className="rounded-xl bg-background px-4 py-3 text-[13px] leading-5 text-muted"><p className="font-semibold text-ink">{FIELD_LABELS[q.field_type]} · {q.required ? "Required" : "Optional"}</p>
      <p>You can edit the wording and guidance. This question&apos;s format is fixed.</p>
      {q.matrix && <p className="mt-1">Monday–Sunday · Morning / Afternoon · {q.matrix.values.join(" / ")}</p>}
    </div> : <>
      <div className="grid items-end gap-4 sm:grid-cols-2"><label className={LABEL}>Answer format<select className={CONTROL} value={q.field_type} onChange={(event) => update((row) => changeFieldType(row, event.target.value as CustomFieldType))}>
        {CUSTOM_TYPES.map((type) => <option key={type} value={type}>{FIELD_LABELS[type]}</option>)}
      </select></label><label className="flex h-11 items-center gap-2 text-sm font-medium text-ink"><input type="checkbox" className="size-4 accent-primary" checked={q.required} onChange={(event) => patch({ required: event.target.checked })} />Required answer</label></div>
      {(q.field_type === "single_choice" || q.field_type === "multi_choice") && <label className={LABEL}>Choices <span className="font-normal">(one per line)</span><textarea className={CONTROL} rows={4} value={draft.choicesText} onChange={(event) => update((row) => ({ ...row, choicesText: event.target.value }))} /></label>}
      {q.field_type === "number" && <div className="grid gap-4 sm:grid-cols-2">
        <label className={LABEL}>Minimum <span className="font-normal">(optional)</span><input type="number" step="any" className={CONTROL} value={draft.minimumText} onChange={(event) => update((row) => ({ ...row, minimumText: event.target.value }))} /></label>
        <label className={LABEL}>Maximum <span className="font-normal">(optional)</span><input type="number" step="any" className={CONTROL} value={draft.maximumText} onChange={(event) => update((row) => ({ ...row, maximumText: event.target.value }))} /></label>
      </div>}
    </>}
    {error && <p id={`error-${q.id}`} role="alert" className="text-sm text-danger">{error}</p>}
  </fieldset></Card>;
}
