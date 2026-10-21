"use client";

import { useRouter } from "next/navigation";
import { useEffect, useTransition } from "react";

const INTERVAL_MS = 3000;

/**
 * Re-reads the job from the server while it is pending or running; stops once it finishes.
 * Each refresh is scheduled only after the previous one has committed. A fixed interval queued refreshes faster
 * than this streamed page renders, so the finished job could stay hidden behind an ever-growing queue.
 */
export function JobRefresher({ active }: { active: boolean }) {
  const router = useRouter();
  const [refreshing, startRefresh] = useTransition();
  useEffect(() => {
    if (!active || refreshing) return;
    const timer = setTimeout(() => startRefresh(() => router.refresh()), INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [active, refreshing, router]);
  return null;
}
