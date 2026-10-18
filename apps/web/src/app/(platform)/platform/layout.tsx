import { redirect } from "next/navigation";
import { type ReactNode, Suspense } from "react";
import { AccessNote, ConsoleShell, type ConsoleNavItem } from "@/components/console/shell";
import { PageSkeleton } from "@/components/console/ui";
import { UserChip, UserChipSkeleton } from "@/components/console/user-chip";
import { ICON_FONT_URL } from "@/components/icon";
import { getAccount, isSystemAdmin } from "@/lib/identity";

const NAV: ConsoleNavItem[] = [
  { href: "/platform", label: "Overview", icon: "dashboard" },
  { href: "/platform/institutions", label: "Institutions", icon: "apartment", detailLabel: "Institution" },
  { href: "/platform/audit", label: "Audit log", icon: "manage_history" },
];

/** The System Admin console. Anyone else is sent to the home page, which explains their own access. */
export default function PlatformLayout({ children }: LayoutProps<"/platform">) {
  return (
    <>
      <link rel="stylesheet" href={ICON_FONT_URL} precedence="default" />
      <ConsoleShell
        section="Platform"
        nav={NAV}
        footer={
          <AccessNote icon="verified_user" title="System Admin access">
            You manage institutions and their admins. Staff, student and timetable data stay with each institution.
          </AccessNote>
        }
        user={
          <Suspense fallback={<UserChipSkeleton />}>
            <UserChip role="System Admin" />
          </Suspense>
        }
      >
        <Suspense fallback={<PageSkeleton />}>
          <SystemAdminOnly>{children}</SystemAdminOnly>
        </Suspense>
      </ConsoleShell>
    </>
  );
}

async function SystemAdminOnly({ children }: { children: ReactNode }) {
  const result = await getAccount();
  if (result.status !== "ok" || !isSystemAdmin(result.account)) redirect("/");
  return children;
}
