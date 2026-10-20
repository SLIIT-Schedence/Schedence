"use client";

import { Activity, useEffect, useState } from "react";
import type { ExamOption } from "@/lib/examination-selection";
import { BothSemestersForm } from "./both-semesters-form";
import { GenerationForm } from "./generation-form";

const MODES = [{ label: "Single Semester", both: false }, { label: "Both Semesters", both: true }] as const;
const isBoth = (search: string) => new URLSearchParams(search).get("semesters") === "both";

// Switching is local state: no navigation, no server render, no data request. Both forms stay mounted
// (hidden by Activity) so each keeps its own selections; the URL is only mirrored for sharing and reload.
export function GenerationModes({ cycles, initialBoth }: { cycles: ExamOption[]; initialBoth: boolean }) {
  const [both, setBoth] = useState(initialBoth);
  const [seen, setSeen] = useState(initialBoth);
  // A real navigation (for example a link to ?semesters=both) hands in a new server value.
  if (seen !== initialBoth) { setSeen(initialBoth); setBoth(initialBoth); }
  useEffect(() => {
    const onPop = () => setBoth(isBoth(window.location.search));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const choose = (next: boolean) => {
    if (next === both) return;
    setBoth(next);
    const url = new URL(window.location.href);
    if (next) url.searchParams.set("semesters", "both"); else url.searchParams.delete("semesters");
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  };
  return <>
    <nav aria-label="Semester generation workflow" className="flex flex-wrap gap-2">
      {MODES.map((item) => <button key={item.label} type="button" aria-pressed={both === item.both} onClick={() => choose(item.both)}
        className={`min-h-11 rounded-xl border px-4 py-3 text-sm font-semibold ${both === item.both ? "border-primary bg-primary text-white" : "border-line bg-surface text-primary-strong"}`}>{item.label}</button>)}
    </nav>
    <Activity mode={both ? "hidden" : "visible"}><GenerationForm cycles={cycles} /></Activity>
    <Activity mode={both ? "visible" : "hidden"}><BothSemestersForm cycles={cycles} /></Activity>
  </>;
}
