"use client";

import { type ReactNode, useActionState } from "react";
import { useToast } from "@/components/console/shell";
import { Icon, type IconName } from "@/components/icon";
import type { FormState } from "@/lib/form-state";

const IDLE: FormState = { status: "idle" };

const BUTTON = {
  primary: "bg-primary text-white hover:bg-primary-strong shadow-[0_8px_20px_-12px_var(--color-primary)]",
  secondary: "border border-line bg-surface text-ink hover:bg-primary-soft",
};

/**
 * A form bound to a server action. Errors stay next to the button; a success shows the console toast. Actions that
 * open another page (run an allocation, save a draft) redirect on the server instead of returning.
 */
export function ActionForm({
  action, children, submitLabel, pendingLabel, icon, variant = "primary", disabledReason, className = "",
}: {
  action: (state: FormState, formData: FormData) => Promise<FormState>;
  children?: ReactNode;
  submitLabel: string;
  pendingLabel?: string;
  icon?: IconName;
  variant?: keyof typeof BUTTON;
  /** When set, the button is disabled and this explains why. */
  disabledReason?: string;
  className?: string;
}) {
  const toast = useToast();
  const [state, formAction, pending] = useActionState(async (previous: FormState, formData: FormData) => {
    const result = await action(previous, formData);
    if (result.status === "success" && result.message) toast(result.message);
    return result;
  }, IDLE);

  return (
    <form action={formAction} className={className} aria-busy={pending}>
      {children}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={Boolean(disabledReason)}
          aria-disabled={pending || undefined}
          onClick={(event) => pending && event.preventDefault()}
          title={disabledReason}
          className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-progress ${BUTTON[variant]}`}
        >
          {pending
            ? <span aria-hidden="true" className="size-4 animate-spin rounded-full border-2 border-current border-r-transparent" />
            : icon && <Icon name={icon} size={18} />}
          {pending ? pendingLabel ?? submitLabel : submitLabel}
        </button>
        {disabledReason && <span className="text-[13px] text-muted">{disabledReason}</span>}
      </div>
      {state.status === "error" && state.message && (
        <p role="alert" className="mt-3 flex gap-2.5 rounded-xl bg-danger-soft px-3.5 py-3 text-sm leading-5 text-danger">
          <Icon name="error" size={18} filled className="mt-px shrink-0" />
          {state.message}
        </p>
      )}
    </form>
  );
}
