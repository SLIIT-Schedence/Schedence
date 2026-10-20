"use client";

import { type ReactNode, useActionState } from "react";
import { Icon } from "@/components/icon";
import { type GenerateState, startGeneration } from "@/lib/teaching-generation-actions";

const INITIAL: GenerateState = { status: "idle" };

/** The explicit Generate click. The server re-checks readiness, so the hidden scope only names the selection. */
export function GenerateForm({ semester, year, stream, rules, back }: { semester: string; year: string; stream: string; rules: string; back: ReactNode }) {
  const [state, action, pending] = useActionState(startGeneration, INITIAL);
  return (
    <form action={action} className="space-y-3">
      <input type="hidden" name="semester" value={semester} />
      <input type="hidden" name="year" value={year} />
      <input type="hidden" name="stream" value={stream} />
      <input type="hidden" name="rules" value={rules} />
      {state.status === "error" && (
        <p role="alert" className="flex gap-2 rounded-xl bg-danger-soft px-3.5 py-3 text-[13px] leading-5 text-danger">
          <Icon name="error" size={18} filled className="mt-px shrink-0" />
          <span>{state.message}</span>
        </p>
      )}
      {pending && (
        <p aria-live="polite" className="text-[13px] text-muted">
          Submitting: the Teaching Service re-validates the inputs before accepting the job. This can take up to a minute and a half.
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        {back}
        <button
          type="submit"
          disabled={pending}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary px-5 text-sm font-semibold text-white transition-colors hover:bg-primary-strong disabled:cursor-wait disabled:opacity-60"
        >
          <Icon name={pending ? "pending_actions" : "event_repeat"} size={18} />
          {pending ? "Submitting…" : "Generate timetable"}
        </button>
      </div>
    </form>
  );
}
