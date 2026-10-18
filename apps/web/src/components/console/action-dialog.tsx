"use client";

import { type ReactNode, useActionState, useCallback, useId, useRef, useState } from "react";
import { Icon, type IconName } from "@/components/icon";
import type { FormState } from "@/lib/form-state";
import { useToast } from "./shell";

export type DialogField = {
  name: string;
  label: string;
  type?: "text" | "email" | "textarea" | "radio" | "select";
  /** For "radio" (shown as choice cards) and "select". */
  options?: { value: string; label: string; description?: string; icon?: IconName }[];
  required?: boolean;
  defaultValue?: string;
  placeholder?: string;
  hint?: string;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  autoComplete?: string;
  /** Monospace and upper-case, for codes. */
  code?: boolean;
};

type Variant = "primary" | "secondary" | "danger" | "icon" | "icon-danger";

const TRIGGER: Record<Variant, string> = {
  primary:
    "inline-flex h-10 items-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white shadow-[0_8px_20px_-12px_var(--color-primary)] transition-colors hover:bg-primary-strong",
  secondary:
    "inline-flex h-10 items-center gap-2 rounded-xl border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:border-secondary/60 hover:bg-background",
  danger:
    "inline-flex h-10 items-center gap-2 rounded-xl border border-danger/20 bg-danger-soft px-4 text-sm font-semibold text-danger transition-colors hover:border-danger/40",
  icon: "grid size-9 place-items-center rounded-lg text-muted transition-colors hover:bg-primary-soft hover:text-primary-strong",
  "icon-danger": "grid size-9 place-items-center rounded-lg text-muted transition-colors hover:bg-danger-soft hover:text-danger",
};

const FIELD =
  "block w-full rounded-xl border border-line bg-surface px-3.5 text-[15px] text-ink placeholder:text-secondary transition-colors hover:border-secondary/70 focus:border-primary aria-invalid:border-danger";

const IDLE: FormState = { status: "idle" };

/**
 * A button that opens a modal form bound to a server action. The dialog closes and shows a toast when the
 * action succeeds; errors stay in the dialog. Each opening starts with a fresh form.
 */
export function ActionDialog({
  label,
  icon,
  variant = "secondary",
  title,
  description,
  fields = [],
  submitLabel,
  tone = "primary",
  action,
  disabledReason,
}: {
  label: string;
  icon?: IconName;
  variant?: Variant;
  title: string;
  description?: ReactNode;
  fields?: DialogField[];
  submitLabel: string;
  tone?: "primary" | "danger";
  action: (state: FormState, formData: FormData) => Promise<FormState>;
  /** When set, the trigger is disabled and this explains why. */
  disabledReason?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [session, setSession] = useState(0);
  const titleId = useId();
  const iconOnly = variant === "icon" || variant === "icon-danger";

  const open = () => {
    setSession((value) => value + 1);
    dialog.current?.showModal();
  };
  const close = useCallback(() => dialog.current?.close(), []);

  return (
    <>
      <button
        type="button"
        onClick={open}
        disabled={Boolean(disabledReason)}
        aria-label={iconOnly ? label : undefined}
        title={disabledReason ?? (iconOnly ? label : undefined)}
        className={`${TRIGGER[variant]} disabled:cursor-not-allowed disabled:opacity-50`}
      >
        {icon && <Icon name={icon} size={iconOnly ? 20 : 18} />}
        {!iconOnly && label}
      </button>

      <dialog
        ref={dialog}
        aria-labelledby={titleId}
        onClick={(event) => event.target === dialog.current && dialog.current?.close()}
        className="m-auto w-[min(30rem,calc(100vw-2rem))] rounded-2xl bg-surface p-0 text-ink shadow-[0_32px_80px_-24px_rgb(29_33_66/0.55)] backdrop:bg-ink/45 backdrop:backdrop-blur-[2px] open:animate-rise-in"
      >
        {session > 0 && (
          <DialogForm
            key={session}
            titleId={titleId}
            title={title}
            description={description}
            fields={fields}
            submitLabel={submitLabel}
            tone={tone}
            action={action}
            onClose={close}
          />
        )}
      </dialog>
    </>
  );
}

function DialogForm({
  titleId,
  title,
  description,
  fields,
  submitLabel,
  tone,
  action,
  onClose,
}: {
  titleId: string;
  title: string;
  description?: ReactNode;
  fields: DialogField[];
  submitLabel: string;
  tone: "primary" | "danger";
  action: (state: FormState, formData: FormData) => Promise<FormState>;
  onClose: () => void;
}) {
  const toast = useToast();
  const fieldPrefix = useId();
  // Close and confirm as soon as the action answers, before the page's refresh lands: the refresh can remove
  // the row this dialog belongs to (a cancelled invitation, a revoked admin) along with this component.
  const [state, formAction, pending] = useActionState(async (previous: FormState, formData: FormData) => {
    const result = await action(previous, formData);
    if (result.status === "success") {
      onClose();
      if (result.message) toast(result.message);
    }
    return result;
  }, IDLE);

  return (
    <form action={formAction}>
      <div className="flex items-start justify-between gap-4 border-b border-line px-6 py-5">
        <div>
          <h2 id={titleId} className="text-lg font-semibold leading-6 text-ink font-stretch-semi-expanded">
            {title}
          </h2>
          {description && <p className="mt-1.5 text-sm leading-5 text-muted">{description}</p>}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="-mr-2 -mt-1 grid size-9 shrink-0 place-items-center rounded-lg text-muted transition-colors hover:bg-background hover:text-ink"
        >
          <Icon name="close" />
        </button>
      </div>

      <div className="space-y-4 px-6 py-5">
        {state.status === "error" && state.message && (
          <p role="alert" className="flex gap-2.5 rounded-xl bg-danger-soft px-3.5 py-3 text-sm leading-5 text-danger">
            <Icon name="error" size={18} filled className="mt-px" />
            {state.message}
          </p>
        )}

        {fields.map((field, index) => {
          const error = state.fieldErrors?.[field.name];
          const id = `${fieldPrefix}-${field.name}`;
          if (field.type === "radio") {
            return <ChoiceField key={field.name} field={field} id={id} error={error} />;
          }
          const common = {
            id,
            name: field.name,
            required: field.required,
            defaultValue: field.defaultValue,
            placeholder: field.placeholder,
            minLength: field.minLength,
            maxLength: field.maxLength,
            autoComplete: field.autoComplete ?? "off",
            autoFocus: index === fields.findIndex((candidate) => candidate.type !== "radio"),
            "aria-invalid": error ? true : undefined,
            "aria-describedby": error || field.hint ? `${id}-help` : undefined,
          };
          return (
            <div key={field.name}>
              <label htmlFor={id} className="mb-1.5 block text-sm font-semibold text-ink">
                {field.label}
                {!field.required && <span className="font-normal text-muted"> (optional)</span>}
              </label>
              {field.type === "textarea" ? (
                <textarea {...common} rows={3} className={`${FIELD} resize-y py-2.5 leading-6`} />
              ) : field.type === "select" ? (
                <span className="relative block">
                  <select {...common} className={`${FIELD} h-11 appearance-none pr-10`}>
                    {field.options?.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <Icon
                    name="chevron_right"
                    size={18}
                    className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rotate-90 text-secondary"
                  />
                </span>
              ) : (
                <input
                  {...common}
                  type={field.type ?? "text"}
                  pattern={field.pattern}
                  className={`${FIELD} h-11 ${field.code ? "font-mono uppercase tracking-wider" : ""}`}
                />
              )}
              {(error || field.hint) && (
                <p id={`${id}-help`} className={`mt-1.5 text-[13px] leading-5 ${error ? "text-danger" : "text-muted"}`}>
                  {error ?? field.hint}
                </p>
              )}
            </div>
          );
        })}
      </div>

      <div className="flex flex-col-reverse gap-2 border-t border-line bg-background/60 px-6 py-4 sm:flex-row sm:justify-end">
        <button
          type="button"
          onClick={onClose}
          className="h-10 rounded-xl border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:bg-background"
        >
          Cancel
        </button>
        <button
          type="submit"
          aria-disabled={pending}
          onClick={(event) => pending && event.preventDefault()}
          className={`inline-flex h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold text-white transition-colors aria-disabled:cursor-progress ${
            tone === "danger" ? "bg-danger hover:bg-danger/90" : "bg-primary hover:bg-primary-strong"
          }`}
        >
          {pending && (
            <span aria-hidden="true" className="size-4 animate-spin rounded-full border-2 border-current border-r-transparent" />
          )}
          {submitLabel}
        </button>
      </div>
    </form>
  );
}

/** A required single choice shown as cards, e.g. the role for a new account. */
function ChoiceField({ field, id, error }: { field: DialogField; id: string; error?: string }) {
  return (
    <fieldset aria-describedby={error || field.hint ? `${id}-help` : undefined}>
      <legend className="mb-2 text-sm font-semibold text-ink">{field.label}</legend>
      <div className="grid gap-2">
        {field.options?.map((option) => (
          <label
            key={option.value}
            className="flex cursor-pointer items-start gap-3 rounded-xl border border-line bg-surface px-3.5 py-3 transition-colors hover:border-secondary/70 has-[:checked]:border-primary has-[:checked]:bg-primary-soft has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-primary"
          >
            <input
              type="radio"
              name={field.name}
              value={option.value}
              required={field.required}
              defaultChecked={field.defaultValue === option.value}
              className="peer sr-only"
            />
            {option.icon && (
              <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-secondary-soft text-secondary-ink peer-checked:bg-primary peer-checked:text-white">
                <Icon name={option.icon} size={20} filled />
              </span>
            )}
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-semibold text-ink">{option.label}</span>
              {option.description && <span className="mt-0.5 block text-[13px] leading-5 text-muted">{option.description}</span>}
            </span>
            <span
              aria-hidden="true"
              className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border-2 border-line text-transparent peer-checked:border-primary peer-checked:bg-primary peer-checked:text-white"
            >
              <span className="size-1.5 rounded-full bg-current" />
            </span>
          </label>
        ))}
      </div>
      {(error || field.hint) && (
        <p id={`${id}-help`} className={`mt-1.5 text-[13px] leading-5 ${error ? "text-danger" : "text-muted"}`}>
          {error ?? field.hint}
        </p>
      )}
    </fieldset>
  );
}
