"use client";

import { Card, EmptyState } from "@/components/console/ui";

export default function GenerationError({ retry }: { retry: () => void }) {
  return (
    <Card>
      <EmptyState icon="error" title="The generation request could not be completed"
        action={<button type="button" onClick={retry} className="rounded-xl border border-line px-4 py-2.5 text-sm font-semibold text-ink hover:bg-background">Reload generation page</button>}>
        The connection may have been interrupted. The solver may still be running; wait before submitting another request.
      </EmptyState>
    </Card>
  );
}
