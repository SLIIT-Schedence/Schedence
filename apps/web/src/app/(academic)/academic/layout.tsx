import { redirect } from "next/navigation";
import { type ReactNode, Suspense } from "react";
import { AccessNote, ConsoleShell, type ConsoleNavItem } from "@/components/console/shell";
import { Card, EmptyState, PageSkeleton } from "@/components/console/ui";
import { UserChip, UserChipSkeleton } from "@/components/console/user-chip";
import { ICON_FONT_URL } from "@/components/icon";
import { SignOutButton } from "@/components/sign-out-button";
import { getAcademicContext } from "@/lib/academic";

const NAV: ConsoleNavItem[] = [
  { href: "/academic", label: "Overview", icon: "dashboard" },
  { href: "/academic/staff", label: "Staff", icon: "badge", detailLabel: "Staff member" },
  { href: "/academic/positions", label: "Positions", icon: "work", detailLabel: "Position" },
  { href: "/academic/timetables", label: "Teaching timetables", icon: "calendar_month" },
  { href: "/academic/forms", label: "Forms", icon: "edit_note", detailLabel: "Form builder" },
  { href: "/academic/assigned-sessions", label: "Assigned sessions", icon: "menu_book" },
  { href: "/academic/workload", label: "Staff workload", icon: "schedule" },
  { href: "/academic/fairness", label: "Workload fairness", icon: "group" },
];

/** Academic Admins also hold rescheduling permissions; that work lives in its own console. */
const LINKS: { label: string; items: ConsoleNavItem[] } = {
  label: "Scheduling",
  items: [{ href: "/adaptive", label: "Adaptive rescheduling", icon: "event_repeat" }],
};

/** The Academic Admin console. Anyone else is sent to the home page, which explains their own access. */
export default function AcademicAdminLayout({ children }: LayoutProps<"/academic">) {
  return (
    <>
      <link rel="stylesheet" href={ICON_FONT_URL} precedence="default" />
      <ConsoleShell
        section="Academic"
        nav={NAV}
        links={LINKS}
        footer={
          <Suspense>
            <InstitutionNote />
          </Suspense>
        }
        user={
          <Suspense fallback={<UserChipSkeleton />}>
            <UserChip role="Academic Admin" />
          </Suspense>
        }
      >
        <Suspense fallback={<PageSkeleton />}>
          <AcademicAdminOnly>{children}</AcademicAdminOnly>
        </Suspense>
      </ConsoleShell>
    </>
  );
}

async function AcademicAdminOnly({ children }: { children: ReactNode }) {
  const context = await getAcademicContext();
  if (!context) redirect("/");
  if (context.institutions.every((institution) => institution.status === "DISABLED")) {
    return (
      <Card>
        <EmptyState icon="domain_disabled" title="Your institution is disabled" action={<SignOutButton variant="primary" />}>
          {context.primary.name} has been disabled on Schedence, so its roles grant no access for now. Contact your
          Institution Admin or the Schedence team.
        </EmptyState>
      </Card>
    );
  }
  return children;
}

async function InstitutionNote() {
  const context = await getAcademicContext();
  if (!context) return null;
  const { institutions } = context;
  return (
    <AccessNote icon="visibility" title={institutions.length === 1 ? institutions[0].name : `${institutions.length} institutions`}>
      You can view staff, positions and administrative hours. Your Institution Admin maintains them.
    </AccessNote>
  );
}
