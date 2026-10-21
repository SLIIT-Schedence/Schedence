"use client";

import { useState, useTransition, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Card } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { createDraftForm } from "@/lib/form-template-actions";
import { formErrorMessage, formsHref, type FormPreset } from "@/lib/form-template-contracts";
import { CONTROL, LABEL, PRIMARY } from "./styles";

export function CreateForm({ institution, presets }: { institution: string; presets: FormPreset[] }) {
  const router = useRouter();
  const [preset, setPreset] = useState<FormPreset["id"] | "">("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [pending, startTransition] = useTransition();
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim() || title.trim().length > 200 || description.length > 4000) {
      setError("Enter a title of 1–200 characters and a description of at most 4,000 characters."); return;
    }
    setError("");
    startTransition(async () => {
      try {
        const result = await createDraftForm({ institution_id: institution, title: title.trim(), description,
          ...(preset ? { preset_id: preset } : {}) });
        if (!result.ok) { setError(formErrorMessage(result.error)); return; }
        router.push(formsHref(institution, result.data.id));
      } catch { setError("The form could not be created. Your entries are kept; try again."); }
    });
  }
  return <Card title="Choose a starting point">
    <form onSubmit={submit} className="space-y-6 p-5">
      <fieldset disabled={pending} className="space-y-6">
        <fieldset className="grid gap-3 md:grid-cols-2"><legend className="sr-only">Starting template</legend>
          <label className={`cursor-pointer rounded-xl border p-4 ${!preset ? "border-primary bg-primary-soft/40" : "border-line"}`}>
            <span className="flex items-center gap-2 text-sm font-semibold text-ink"><input type="radio" name="preset" value="" checked={!preset} className="accent-primary"
              onChange={() => { setPreset(""); setTitle(""); setDescription(""); }} />Blank form</span>
            <span className="mt-2 block text-[13px] leading-5 text-muted">Build a draft with your own questions.</span>
          </label>
          {presets.map((item) => <label key={item.id} className={`cursor-pointer rounded-xl border p-4 ${preset === item.id ? "border-primary bg-primary-soft/40" : "border-line"}`}>
            <span className="flex items-center gap-2 text-sm font-semibold text-ink"><input type="radio" name="preset" value={item.id} checked={preset === item.id} className="accent-primary"
              onChange={() => { setPreset(item.id); setTitle(item.title); setDescription(item.description); }} />{item.title}</span>
            <span className="mt-2 block text-[13px] leading-5 text-muted">{item.questions.length} questions, including day-part availability, module preferences and preferred weekly hours.</span>
          </label>)}
        </fieldset>
        <label className={LABEL}>Form title<input className={CONTROL} value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} required autoComplete="off" /></label>
        <label className={LABEL}>Description <span className="font-normal">(optional)</span><textarea className={CONTROL} rows={3} value={description} onChange={(event) => setDescription(event.target.value)} maxLength={4000} /></label>
      </fieldset>
      {error && <p role="alert" className="rounded-xl border border-danger/20 bg-danger-soft px-4 py-3 text-sm text-danger">{error}</p>}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-5">
        <p className="text-[13px] text-muted">You can edit questions and preview the form after creating it.</p>
        <button type="submit" className={PRIMARY} disabled={pending}><Icon name="edit_note" size={18} />{pending ? "Creating…" : "Create draft"}</button>
      </div>
    </form>
  </Card>;
}
