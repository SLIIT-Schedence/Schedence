import { Suspense, type ReactNode } from "react";
import { Card, EmptyState, PageSkeleton } from "@/components/console/ui";
import { getDutyStatus } from "@/lib/duty-allocation";
import { DutyReadError } from "./_components/duty-ui";

// Renders inside the Examination console (../layout.tsx), which already requires the Examination Admin role.
// The duty pages are listed in the console sidebar (../_navigation.ts).
export default function DutyAllocationLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <EnabledOnly>{children}</EnabledOnly>
    </Suspense>
  );
}

async function EnabledOnly({ children }: { children: ReactNode }) {
  const status = await getDutyStatus();
  if (status.ok && status.data.enabled) return children;
  return (
    <Card>
      {!status.ok && status.error.status !== 404 ? (
        <DutyReadError error={status.error} what="staff duty allocation" />
      ) : (
        <EmptyState icon="block" title="Staff duty allocation is not enabled">
          {status.ok
            ? status.data.problem ?? "The duty-allocation data source is turned off."
            : "The Examination Service is running without the duty-allocation API. It is turned on with the M3_DEMO_SOURCE setting."}
        </EmptyState>
      )}
    </Card>
  );
}
