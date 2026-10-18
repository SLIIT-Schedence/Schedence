"use client";

import type { ReactNode } from "react";
import { useFormStatus } from "react-dom";

/** A form's submit button that shows `pendingLabel` and ignores extra clicks while the action runs. */
export function SubmitButton({
  children,
  pendingLabel,
  className,
}: {
  children: ReactNode;
  pendingLabel: string;
  className: string;
}) {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      aria-disabled={pending}
      onClick={(event) => pending && event.preventDefault()}
      className={`${className} aria-disabled:cursor-progress`}
    >
      {pending ? (
        <>
          <span
            aria-hidden="true"
            className="size-4 animate-spin rounded-full border-2 border-current border-r-transparent"
          />
          {pendingLabel}
        </>
      ) : (
        children
      )}
    </button>
  );
}
