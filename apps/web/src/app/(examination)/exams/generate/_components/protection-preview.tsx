"use client";

import { useEffect, useState } from "react";
import { loadProtectionPreview } from "@/lib/examination-protection-actions";
import type { ProtectionPreview as Preview } from "@/lib/examination-protection";
import { ProtectedTimetables } from "./protected-timetables";
import { secondaryButton } from "./period-calendar";

/** Keyed by the parent calendar/break so stale responses never describe newly selected dates. */
export function ProtectionPreview({ periodCode, start, end, minimumBreak, disabled }: {
  periodCode: string; start: string; end: string; minimumBreak: number; disabled: boolean;
}) {
  const [state, setState] = useState<{ data?: Preview; error?: string } | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    loadProtectionPreview(periodCode, start, end, minimumBreak).then((result) => {
      if (active) setState(result.ok ? { data: result.data } : { error: result.error.status === 403
        ? "Your account cannot read protected timetables. Protection preview is unavailable."
        : "Protected timetables could not be loaded. Protection preview is unavailable; retry before reviewing the slots." });
    }).catch(() => { if (active) setState({ error: "The protection preview connection was interrupted. Retry to review occupied slots." }); });
    return () => { active = false; };
  }, [periodCode, start, end, minimumBreak, refresh]);
  return <div className="space-y-2">
    {!state ? <p role="status" className="rounded-xl bg-primary-soft p-4 text-sm text-primary-strong">Checking protected timetables and occupied exam slots…</p>
      : state.error ? <p role="alert" className="rounded-xl bg-secondary-soft p-4 text-sm text-secondary-ink">{state.error}</p>
        : <ProtectedTimetables data={state.data} />}
    <button type="button" disabled={!state || disabled} className={secondaryButton} onClick={() => { setState(null); setRefresh((value) => value + 1); }}>Refresh protected timetables</button>
  </div>;
}
