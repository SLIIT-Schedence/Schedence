/** Schedence mark: three staggered blocks, like sessions on a timetable. Keep in sync with src/app/icon.svg. */
export function SchedenceMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={className}>
      <rect width="32" height="32" rx="9" fill="var(--color-primary)" />
      <rect x="8" y="8.5" width="16" height="4" rx="2" fill="#ffffff" />
      <rect x="8" y="14" width="10" height="4" rx="2" fill="var(--color-accent)" />
      <rect x="13" y="19.5" width="11" height="4" rx="2" fill="#ffffff" fillOpacity="0.72" />
    </svg>
  );
}

export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <SchedenceMark className="size-8 shrink-0" />
      <span className="text-[1.1875rem] font-semibold tracking-[-0.01em] text-ink font-stretch-semi-expanded">
        Schedence
      </span>
    </span>
  );
}
