import { Suspense, type ReactNode } from "react";
import { AccessNote, ConsoleShell } from "@/components/console/shell";
import { PageSkeleton } from "@/components/console/ui";
import { UserChip, UserChipSkeleton } from "@/components/console/user-chip";
import { ICON_FONT_URL } from "@/components/icon";
import { requireExaminationAdmin } from "@/lib/examination-access";
import { EXAM_NAV } from "./_navigation";

export default function ExaminationLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <link rel="stylesheet" href={ICON_FONT_URL} precedence="default" />
      <Suspense fallback={<div className="p-6 lg:p-8"><PageSkeleton /></div>}>
        <ExaminationAdminOnly>{children}</ExaminationAdminOnly>
      </Suspense>
    </>
  );
}

async function ExaminationAdminOnly({ children }: { children: ReactNode }) {
  await requireExaminationAdmin();
  return (
    <ConsoleShell
      section="Examinations"
      nav={EXAM_NAV}
      user={<Suspense fallback={<UserChipSkeleton />}><UserChip role="Examination Admin" /></Suspense>}
      footer={
        <AccessNote icon="verified_user" title="Examination Admin">
          Generate and save FINAL timetables, then approve and publish them. The Examination Service checks every permission.
        </AccessNote>
      }
    >
      {children}
    </ConsoleShell>
  );
}
