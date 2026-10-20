"use client";

import Link from "next/link";
import { Badge, Card } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import type { GenerationMode, SaveDraftState } from "@/lib/examination-generation-types";
import { primaryButton } from "./period-calendar";

/**
 * Generate & Save Draft. Saving asks the Examination Service for a NEW generation, validated again and stored
 * as a DRAFT; it is not the preview on screen. The admin must confirm that before the button is enabled.
 */
export function SaveDraftCard({ periodName, mode, timeLimit, confirmed, onConfirm, onSave, disabled, saving, state }: {
  periodName: string;
  mode: GenerationMode;
  timeLimit: string;
  confirmed: boolean;
  onConfirm: (value: boolean) => void;
  onSave: () => void;
  disabled: boolean;
  saving: boolean;
  state: SaveDraftState;
}) {
  return (
    <Card title="Generate & Save Draft" description="Keep a validated timetable for approval." actions={<Badge tone="primary">Saved as DRAFT</Badge>}>
      <div className="space-y-4 p-5 text-sm leading-6 text-muted">
        <ul className="list-disc space-y-1 pl-5">
          <li><strong className="text-ink">Saving runs a fresh generation.</strong> It uses {periodName}, {mode === "optimized" ? "Optimized" : "Feasibility"} mode
            and {timeLimit ? `a ${timeLimit}-second limit` : "the saved solver limit"}. The saved timetable can differ from any preview on this page.</li>
          <li>The result is validated again against H1–H11 and stored as a new draft. Earlier drafts are kept.</li>
          <li>Saving does not approve or publish anything. You can review the draft before approving it.</li>
        </ul>
        <label className="flex items-start gap-3 rounded-xl border border-line p-4 text-ink">
          <input type="checkbox" checked={confirmed} disabled={disabled} onChange={(event) => onConfirm(event.target.checked)} className="mt-1 accent-primary" />
          <span>I understand that saving runs a new generation and stores its result as a draft timetable.</span>
        </label>
        <button type="button" onClick={onSave} disabled={disabled || !confirmed} aria-busy={saving} className={primaryButton}>
          <Icon name="save" size={18} />{saving ? "Generating and saving…" : "Generate & Save Draft"}
        </button>
        {state.status === "error" && (
          <div role="alert" className="rounded-xl bg-danger-soft p-4 text-danger">
            <h3 className="font-semibold">{state.title}</h3>
            <p>{state.httpStatus ? `HTTP ${state.httpStatus}: ` : ""}{state.message}</p>
            <Link href="/exams/timetables" className="mt-2 inline-flex font-semibold underline underline-offset-2">Open saved timetables</Link>
          </div>
        )}
        {state.status === "saved" && (
          <p role="status" className="rounded-xl bg-accent p-4 text-accent-ink">
            Draft #{state.draftNumber} saved. Opening it…{" "}
            <Link href={`/exams/timetables/${state.timetableId}`} className="font-semibold underline underline-offset-2">Open draft</Link>
          </p>
        )}
      </div>
    </Card>
  );
}
