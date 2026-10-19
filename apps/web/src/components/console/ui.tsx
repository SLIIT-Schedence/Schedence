import Link from "next/link";
import type { ReactNode } from "react";
import { Icon, type IconName } from "@/components/icon";
import type { Tone } from "@/lib/audit-events";
import type { ApiError } from "@/lib/identity-api";

// Building blocks shared by the console pages. Server-safe: no hooks, no client state.

const dateFormat = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "Asia/Colombo" });
const dateTimeFormat = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Asia/Colombo",
});
const timeFormat = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Colombo" });

export const formatDate = (iso: string) => dateFormat.format(new Date(iso));
export const formatDateTime = (iso: string) => dateTimeFormat.format(new Date(iso));
export const formatTime = (iso: string) => timeFormat.format(new Date(iso));

export function initials(name: string): string {
  const words = name.split(/[\s@._-]+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : name.slice(0, 2)).toUpperCase();
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-[1.75rem] font-semibold leading-tight tracking-[-0.02em] text-ink font-stretch-semi-expanded sm:text-[2rem]">
          {title}
        </h1>
        {description && <p className="mt-1.5 max-w-2xl text-[15px] leading-6 text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  description,
  actions,
  children,
  className = "",
}: {
  title?: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`overflow-hidden rounded-2xl border border-line bg-surface ${className}`}>
      {title && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
          <div>
            <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
            {description && <p className="mt-0.5 text-[13px] text-muted">{description}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

const TONE: Record<Tone, string> = {
  primary: "bg-primary-soft text-primary-strong",
  accent: "bg-accent text-accent-ink",
  danger: "bg-danger-soft text-danger",
  neutral: "bg-secondary-soft text-secondary-ink",
};

export function ToneIcon({ icon, tone, size = "md" }: { icon: IconName; tone: Tone; size?: "sm" | "md" | "lg" }) {
  const box = { sm: "size-8 rounded-lg", md: "size-10 rounded-xl", lg: "size-12 rounded-2xl" }[size];
  return (
    <span className={`grid shrink-0 place-items-center ${box} ${TONE[tone]}`}>
      <Icon name={icon} size={size === "sm" ? 18 : size === "md" ? 20 : 24} filled />
    </span>
  );
}

/** An institution's code as a small square badge, in place of a logo. */
export function CodeTile({ code, size = "md" }: { code: string; size?: "md" | "lg" }) {
  const short = code.length <= 5 ? code : code.slice(0, 4);
  return (
    <span
      className={`grid shrink-0 place-items-center bg-primary-soft font-mono font-semibold tracking-tight text-primary-strong ${
        size === "lg" ? "size-14 rounded-2xl text-[13px]" : "size-10 rounded-xl text-[10px]"
      }`}
    >
      {short}
    </span>
  );
}

export function StatusBadge({ status }: { status: "ACTIVE" | "DISABLED" }) {
  const active = status === "ACTIVE";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${
        active ? "bg-accent text-accent-ink" : "bg-danger-soft text-danger"
      }`}
    >
      <span aria-hidden="true" className={`size-1.5 rounded-full ${active ? "bg-accent-ink" : "bg-danger"}`} />
      {active ? "Active" : "Disabled"}
    </span>
  );
}

/** A small status pill with a dot; the label always names the state, so colour is never the only cue. */
export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  const dot = { primary: "bg-primary-strong", accent: "bg-accent-ink", danger: "bg-danger", neutral: "bg-secondary-ink" }[tone];
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-semibold ${TONE[tone]}`}>
      <span aria-hidden="true" className={`size-1.5 rounded-full ${dot}`} />
      {children}
    </span>
  );
}

export function Kpi({
  icon,
  tone,
  label,
  value,
  hint,
  href,
}: {
  icon: IconName;
  tone: Tone;
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  href?: string;
}) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[13px] font-semibold text-muted sm:text-sm">{label}</p>
        <ToneIcon icon={icon} tone={tone} size="sm" />
      </div>
      <p className="mt-2 text-[1.625rem] font-semibold leading-none tracking-tight text-ink font-stretch-semi-expanded sm:text-[2rem]">
        {value}
      </p>
      {hint && <p className="mt-2 text-xs leading-4 text-muted sm:text-[13px]">{hint}</p>}
    </>
  );
  const className = "block min-w-0 rounded-2xl border border-line bg-surface p-4 sm:p-5";
  return href ? (
    <Link href={href} className={`${className} transition-colors hover:border-primary/40 hover:shadow-[0_12px_32px_-20px_rgb(29_33_66/0.35)]`}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

export function EmptyState({
  icon = "inbox",
  title,
  children,
  action,
}: {
  icon?: IconName;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      <ToneIcon icon={icon} tone="neutral" size="lg" />
      <p className="mt-4 text-[15px] font-semibold text-ink">{title}</p>
      {children && <p className="mt-1 max-w-sm text-sm leading-5 text-muted">{children}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/** Shown in place of data that failed to load. */
export function LoadError({
  error,
  what,
  retryHref,
  service = "identity",
}: {
  error: ApiError;
  what: string;
  retryHref: string;
  /** The backend that failed, as in "The identity service didn't respond". */
  service?: string;
}) {
  const message =
    error.status === 0 || error.status >= 500
      ? `The ${service} service didn't respond. Check that it's running, then try again.`
      : error.status === 403
        ? "Your account doesn't have permission to view this."
        : error.message;
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      <ToneIcon icon="error" tone="danger" size="lg" />
      <p className="mt-4 text-[15px] font-semibold text-ink">Couldn&apos;t load {what}</p>
      <p className="mt-1 max-w-sm text-sm leading-5 text-muted">{message}</p>
      <Link
        href={retryHref}
        prefetch={false}
        className="mt-5 inline-flex h-10 items-center gap-2 rounded-xl border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:bg-background"
      >
        <Icon name="refresh" size={18} />
        Try again
      </Link>
    </div>
  );
}

export function Pagination({
  page,
  pageSize,
  total,
  hrefFor,
}: {
  page: number;
  pageSize: number;
  total: number;
  hrefFor: (page: number) => string;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total === 0) return null;
  const first = (page - 1) * pageSize + 1;
  const last = Math.min(total, page * pageSize);
  const step = (target: number, icon: IconName, label: string) =>
    target < 1 || target > pages ? (
      <span aria-hidden="true" className="grid size-9 place-items-center rounded-lg text-line">
        <Icon name={icon} />
      </span>
    ) : (
      <Link
        href={hrefFor(target)}
        aria-label={label}
        className="grid size-9 place-items-center rounded-lg border border-line bg-surface text-ink transition-colors hover:border-primary/40 hover:text-primary-strong"
      >
        <Icon name={icon} />
      </Link>
    );

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-5 py-3 text-[13px] text-muted">
      <p>
        Showing <span className="font-semibold text-ink">{first}</span>–<span className="font-semibold text-ink">{last}</span> of{" "}
        <span className="font-semibold text-ink">{total}</span>
      </p>
      <nav aria-label="Pagination" className="flex items-center gap-2">
        {step(page - 1, "chevron_left", "Previous page")}
        <span className="min-w-16 text-center font-mono text-xs">
          {page} / {pages}
        </span>
        {step(page + 1, "chevron_right", "Next page")}
      </nav>
    </div>
  );
}

export function Skeleton({ className }: { className: string }) {
  return <div className={`animate-pulse rounded-lg bg-line/60 ${className}`} />;
}

export function PageSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading">
      <Skeleton className="h-9 w-64" />
      <Skeleton className="mt-3 h-4 w-96 max-w-full" />
      <div className="mt-8 grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        {[0, 1, 2, 3].map((key) => (
          <Skeleton key={key} className="h-32 rounded-2xl" />
        ))}
      </div>
      <Skeleton className="mt-6 h-80 rounded-2xl" />
    </div>
  );
}

export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading" className="divide-y divide-line">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex items-center gap-4 px-5 py-4">
          <Skeleton className="size-10 rounded-xl" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-3 w-1/5" />
          </div>
          <Skeleton className="h-6 w-20 rounded-full" />
        </div>
      ))}
    </div>
  );
}

/** A plain query string from the given values, skipping empty ones. */
export function query(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "" && !(key === "page" && value === 1)) params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value);

export function pageNumber(value: string | string[] | undefined): number {
  const page = Number.parseInt(first(value) ?? "1", 10);
  return Number.isFinite(page) && page >= 1 && page <= 100_000 ? page : 1;
}
