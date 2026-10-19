"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/icon";

export function RefreshButton({ label = "Refresh overview" }: { label?: string }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return (
    <button
      type="button"
      onClick={() => startTransition(() => router.refresh())}
      disabled={pending}
      aria-busy={pending}
      className="inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:bg-primary-soft disabled:cursor-wait disabled:opacity-60"
    >
      <Icon name="refresh" size={18} />{pending ? "Refreshing…" : label}
    </button>
  );
}
