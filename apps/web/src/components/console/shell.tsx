"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, type ReactNode, Suspense, useCallback, useContext, useEffect, useState } from "react";
import { Icon, type IconName } from "@/components/icon";
import { Wordmark } from "@/components/wordmark";

export type ConsoleNavItem = {
  href: string;
  label: string;
  icon: IconName;
  /** The breadcrumb for pages below this one, such as "Institution" under Institutions. */
  detailLabel?: string;
};

function isActive(pathname: string, item: ConsoleNavItem, home: string) {
  return item.href === home ? pathname === home : pathname === item.href || pathname.startsWith(`${item.href}/`);
}

function crumbsFor(pathname: string, nav: ConsoleNavItem[], home: string): string[] {
  const item = nav.findLast((candidate) => isActive(pathname, candidate, home));
  if (!item) return [nav[0]?.label ?? ""];
  return item.detailLabel && pathname !== item.href ? [item.label, item.detailLabel] : [item.label];
}

/**
 * The frame shared by the admin consoles: a sidebar (a drawer below the lg breakpoint), a sticky top bar
 * and the toast region. `user` is the signed-in user's chip and `footer` the sidebar's note, both rendered
 * on the server. The parts that read the URL sit in their own Suspense boundaries, because the URL is
 * request data on dynamic routes.
 */
export function ConsoleShell({
  section,
  nav,
  footer,
  user,
  children,
}: {
  /** The console's name in the sidebar and breadcrumb, e.g. "Platform". */
  section: string;
  /** The first item is the console's home. */
  nav: ConsoleNavItem[];
  footer?: ReactNode;
  user: ReactNode;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const home = nav[0]?.href ?? "/";

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <ToastProvider>
      <div className="min-h-dvh lg:grid lg:grid-cols-[16.5rem_minmax(0,1fr)]">
        <div
          aria-hidden="true"
          onClick={() => setOpen(false)}
          className={`fixed inset-0 z-40 bg-ink/40 backdrop-blur-[2px] transition-opacity duration-300 lg:hidden ${
            open ? "opacity-100" : "pointer-events-none opacity-0"
          }`}
        />

        <aside
          id="console-sidebar"
          aria-label={`${section} navigation`}
          className={`fixed inset-y-0 left-0 z-50 flex w-[17rem] max-w-[85vw] flex-col border-r border-line bg-surface transition-[transform,visibility] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] lg:sticky lg:top-0 lg:z-auto lg:h-dvh lg:w-auto lg:max-w-none lg:translate-x-0 lg:visible ${
            open ? "visible translate-x-0 shadow-[0_24px_60px_-20px_rgb(29_33_66/0.45)]" : "invisible -translate-x-full"
          }`}
        >
          <div className="flex h-16 shrink-0 items-center justify-between gap-2 border-b border-line px-5">
            <Link href={home} onClick={close} className="rounded-lg">
              <Wordmark />
            </Link>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close navigation"
              className="grid size-9 place-items-center rounded-lg text-muted transition-colors hover:bg-background hover:text-ink lg:hidden"
            >
              <Icon name="close" />
            </button>
          </div>

          <nav className="flex-1 overflow-y-auto px-3 py-5">
            <p className="px-3 pb-2 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-secondary">{section}</p>
            <Suspense fallback={<NavLinks nav={nav} home={home} onNavigate={close} />}>
              <ActiveNavLinks nav={nav} home={home} onNavigate={close} />
            </Suspense>
          </nav>

          {footer && <div className="m-3">{footer}</div>}
        </aside>

        <div className="flex min-w-0 flex-col">
          <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-line bg-surface/90 px-4 backdrop-blur sm:px-6 lg:px-8">
            <button
              type="button"
              onClick={() => setOpen(true)}
              aria-label="Open navigation"
              aria-controls="console-sidebar"
              aria-expanded={open}
              className="-ml-1 grid size-10 place-items-center rounded-lg text-ink transition-colors hover:bg-background lg:hidden"
            >
              <Icon name="menu" size={22} />
            </button>
            <p className="flex min-w-0 items-center gap-1.5 text-sm">
              <span className="hidden text-muted sm:inline">{section}</span>
              <Suspense>
                <Breadcrumbs nav={nav} home={home} />
              </Suspense>
            </p>
            <div className="ml-auto">{user}</div>
          </header>

          <main className="w-full max-w-[90rem] flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children}</main>
        </div>
      </div>
    </ToastProvider>
  );
}

type NavProps = { nav: ConsoleNavItem[]; home: string; onNavigate: () => void };

function ActiveNavLinks(props: NavProps) {
  return <NavLinks {...props} pathname={usePathname()} />;
}

/** The sidebar links; without `pathname` (while it streams in) none is marked active. */
function NavLinks({ nav, home, onNavigate, pathname }: NavProps & { pathname?: string }) {
  return (
    <ul className="space-y-1">
      {nav.map((item) => {
        const active = pathname !== undefined && isActive(pathname, item, home);
        return (
          <li key={item.href}>
            <Link
              href={item.href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={`flex h-11 items-center gap-3 rounded-xl px-3 text-[14px] font-semibold transition-colors ${
                active
                  ? "bg-primary text-white shadow-[0_8px_20px_-12px_var(--color-primary)]"
                  : "text-muted hover:bg-background hover:text-ink"
              }`}
            >
              <Icon name={item.icon} filled={active} />
              {item.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function Breadcrumbs({ nav, home }: { nav: ConsoleNavItem[]; home: string }) {
  const crumbs = crumbsFor(usePathname(), nav, home);
  return crumbs.map((crumb, index) => (
    <span key={crumb} className={`min-w-0 items-center gap-1.5 ${index === crumbs.length - 1 ? "flex" : "hidden sm:flex"}`}>
      <Icon name="chevron_right" size={16} className="hidden text-secondary sm:inline-block" />
      <span className={`truncate ${index === crumbs.length - 1 ? "font-semibold text-ink" : "text-muted"}`}>{crumb}</span>
    </span>
  ));
}

/** The sidebar's note about what this console's role may do. */
export function AccessNote({ icon, title, children }: { icon: IconName; title: string; children: ReactNode }) {
  return (
    <div className="rounded-2xl bg-accent p-4 text-[13px] leading-5 text-accent-ink">
      <p className="flex items-center gap-2 font-semibold">
        <Icon name={icon} size={18} filled />
        {title}
      </p>
      <div className="mt-1.5 text-accent-ink/85">{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- toasts

type Toast = { id: number; message: string; tone: "success" | "error" };

const ToastContext = createContext<(message: string, tone?: Toast["tone"]) => void>(() => {});

export const useToast = () => useContext(ToastContext);

function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const show = useCallback((message: string, tone: Toast["tone"] = "success") => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current.slice(-2), { id, message, tone }]);
    setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 4500);
  }, []);

  return (
    <ToastContext value={show}>
      {children}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed inset-x-4 bottom-4 z-[60] flex flex-col items-center gap-2 sm:inset-x-auto sm:right-6 sm:items-end"
      >
        {toasts.map((toast) => (
          <p
            key={toast.id}
            className={`pointer-events-auto flex w-full max-w-sm animate-rise-in items-start gap-2.5 rounded-xl px-4 py-3 text-sm font-medium leading-5 shadow-[0_16px_40px_-16px_rgb(29_33_66/0.5)] ${
              toast.tone === "success" ? "bg-ink text-white" : "bg-danger text-white"
            }`}
          >
            <Icon name={toast.tone === "success" ? "check_circle" : "error"} size={18} filled className="mt-px" />
            {toast.message}
          </p>
        ))}
      </div>
    </ToastContext>
  );
}
