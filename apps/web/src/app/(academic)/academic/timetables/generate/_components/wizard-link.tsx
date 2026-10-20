"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import type { ReactNode } from "react";

/** Keep the latest draft even when a rule edit updated native history without a server navigation. */
export function WizardLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  const search = useSearchParams();
  const rules = search.get("rules");
  if (rules && href.startsWith("/academic/timetables/generate")) {
    const [pathname, raw] = href.split("?");
    const params = new URLSearchParams(raw);
    params.set("rules", rules);
    href = `${pathname}?${params}`;
  }
  return <Link href={href} prefetch={false} className={className}>{children}</Link>;
}
