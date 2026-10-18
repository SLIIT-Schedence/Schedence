import { redirect } from "next/navigation";
import { type ReactNode, Suspense } from "react";
import { AccessNote, ConsoleShell, type ConsoleNavItem } from "@/components/console/shell";
import { Card, EmptyState, PageSkeleton } from "@/components/console/ui";
import { UserChip, UserChipSkeleton } from "@/components/console/user-chip";
import { ICON_FONT_URL } from "@/components/icon";
import { SignOutButton } from "@/components/sign-out-button";
import { getAdminContext } from "@/lib/institution";

const NAV: ConsoleNavItem[] = [
  { href: "/admin", label: "Overview", icon: "dashboard" },
  { href: "/admin/users", label: "People", icon: "group" },
  { href: "/admin/invitations", label: "Invitations", icon: "forward_to_inbox" },
];

/** The Institution Admin console. Anyone else is sent to the home page, which explains their own access. */
export default function InstitutionAdminLayout({ children }: LayoutProps<"/admin">) {
  return (
    <>
      <link rel="stylesheet" href={ICON_FONT_URL} precedence="default" />
      <ConsoleShell
        section="Institution"
        nav={NAV}
        footer={
          <Suspense>
            <InstitutionNote />
          </Suspense>
        }
        user={
          <Suspense fallback={<UserChipSkeleton />}>
            <UserChip role="Institution Admin" />
          </Suspense>
        }
      >
        <Suspense fallback={<PageSkeleton />}>
          <InstitutionAdminOnly>{children}</InstitutionAdminOnly>
        </Suspense>
      </ConsoleShell>
    </>
  );
}

async function InstitutionAdminOnly({ children }: { children: ReactNode }) {
  const context = await getAdminContext();
  if (!context) redirect("/");
  if (context.institutions.every((institution) => institution.status === "DISABLED")) {
    return (
      <Card>
        <EmptyState icon="domain_disabled" title="Your institution is disabled" action={<SignOutButton variant="primary" />}>
          {context.primary.name} has been disabled on Schedence, so its roles grant no access for now. Contact the
          Schedence team to re-enable it.
        </EmptyState>
      </Card>
    );
  }
  return children;
}

async function InstitutionNote() {
  const context = await getAdminContext();
  if (!context) return null;
  const { institutions } = context;
  return (
    <AccessNote icon="apartment" title={institutions.length === 1 ? institutions[0].name : `${institutions.length} institutions`}>
      {institutions.length === 1 ? (
        <>
          <span className="font-mono text-xs">{institutions[0].code}</span> · You manage its accounts and roles.
        </>
      ) : (
        "You manage accounts and roles for each of them."
      )}
    </AccessNote>
  );
}
