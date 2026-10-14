"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Re-reads the job from the server every few seconds while it is pending or running; stops once it finishes. */
export function JobRefresher({ active }: { active: boolean }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => router.refresh(), 3000);
    return () => clearInterval(timer);
  }, [active, router]);
  return null;
}
