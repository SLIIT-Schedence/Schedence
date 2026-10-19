import { ToneIcon } from "@/components/console/ui";
import type { ExaminationApiError } from "@/lib/examination-api";
import { RefreshButton } from "./refresh-button";

export function ExaminationReadError({ error, what }: { error: ExaminationApiError; what: string }) {
  return (
    <div role="status" className="flex flex-col items-center px-5 py-8 text-center">
      <ToneIcon icon="error" tone="danger" />
      <p className="mt-3 text-sm font-semibold text-ink">Couldn&apos;t load {what}</p>
      <p className="mt-1 max-w-md text-sm leading-5 text-muted">
        {error.status === 403 ? "Your account does not have access to this examination data. Ask your institution administrator to check your permissions." : error.message}
      </p>
      <div className="mt-4"><RefreshButton label="Try again" /></div>
    </div>
  );
}
