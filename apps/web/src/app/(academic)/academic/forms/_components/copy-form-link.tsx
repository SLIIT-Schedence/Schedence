"use client";

import { useState } from "react";
import { CONTROL, LABEL, SECONDARY } from "./styles";

export function CopyFormLink({ url }: { url: string }) {
  const [message, setMessage] = useState("");
  async function copy() {
    try { await navigator.clipboard.writeText(url); setMessage("Link copied."); }
    catch { setMessage("Copy is unavailable. Select the link below and copy it manually."); }
  }
  return <div className="space-y-2">
    <label className={LABEL}>Shareable link<input className={CONTROL} value={url} readOnly onFocus={(event) => event.currentTarget.select()} /></label>
    <button type="button" className={SECONDARY} onClick={copy}>Copy shareable link</button>
    <p role="status" className="text-sm text-muted">{message || "Only authorized, signed-in recipients can access this link."}</p>
  </div>;
}
