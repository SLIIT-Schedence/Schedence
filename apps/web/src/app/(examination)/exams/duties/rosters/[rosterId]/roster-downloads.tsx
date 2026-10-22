"use client";

import { useState } from "react";
import { Icon } from "@/components/icon";

export function RosterDownloads({ rosterId, rowVersion }: { rosterId: string; rowVersion: number }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function download(format: "xlsx" | "pdf") {
    setError(null); setBusy(format);
    try {
      const response = await fetch(`/exams/duties/rosters/${rosterId}/download/${format}?version=${rowVersion}`, { cache: "no-store", signal: AbortSignal.timeout(110_000) });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.message ?? "The roster download failed. Please try again.");
      }
      const type = response.headers.get("Content-Type") ?? "";
      if (!(format === "pdf" ? type.includes("application/pdf") : type.includes("spreadsheetml.sheet"))) throw new Error("The download session expired or returned an invalid file. Sign in again and retry.");
      const blob = await response.blob();
      if (!blob.size) throw new Error("The roster file was empty. Please try again.");
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = `Schedence-duty-roster-${rosterId}-revision${rowVersion}.${format}`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (cause) {
      setError(cause instanceof Error && cause.name !== "TimeoutError" ? cause.message : "The download took too long. Please try again.");
    } finally { setBusy(null); }
  }
  return <div className="space-y-2">
    <div className="flex flex-wrap gap-2">
      {(["xlsx", "pdf"] as const).map((format) => <button key={format} type="button" disabled={busy !== null} onClick={() => download(format)}
        className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-line bg-surface px-4 py-2 text-sm font-semibold text-ink hover:bg-background disabled:cursor-wait disabled:opacity-60">
        <Icon name="download" size={18} />
        {busy === format ? "Preparing…" : format === "xlsx" ? "Download Excel" : "Download PDF"}
      </button>)}
    </div>
    {error && <p role="alert" className="max-w-xl text-sm text-danger">{error}</p>}
  </div>;
}
