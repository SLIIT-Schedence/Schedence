import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense, type ReactNode } from "react";
import { SignOutButton } from "@/components/sign-out-button";
import { isAcademicAdmin } from "@/lib/academic";
import { type Account, getAccount, isSystemAdmin } from "@/lib/identity";
import { isInstitutionAdmin } from "@/lib/institution";

export const metadata: Metadata = { title: "Home" };

const assignedDate = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "Asia/Colombo" });

export default function HomePage() {
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-10 sm:px-6 sm:py-14">
      <Suspense fallback={<AccountSkeleton />}>
        <AccountOverview />
      </Suspense>
    </div>
  );
}

async function AccountOverview() {
  const result = await getAccount();

  switch (result.status) {
    case "ok":
      // Admins work in their consoles, which have their own navigation.
      if (isSystemAdmin(result.account)) redirect("/platform");
      if (isInstitutionAdmin(result.account)) redirect("/admin");
      if (isAcademicAdmin(result.account)) redirect("/academic");
      return <Welcome account={result.account} />;
    case "not-provisioned":
      return (
        <AccountProblem title="Your Schedence account isn't set up yet" actions={<SignOutButton variant="primary" />}>
          You signed in{result.email ? ` as ${result.email}` : ""}, but no Schedence account is linked to it. Ask your
          institution&apos;s Schedence administrator to add you, then sign in again.
        </AccountProblem>
      );
    case "disabled":
      return (
        <AccountProblem title="This Schedence account is disabled" actions={<SignOutButton variant="primary" />}>
          Access{result.email ? ` for ${result.email}` : ""} has been turned off. Ask your Schedence administrator to
          re-enable it, then sign in again.
        </AccountProblem>
      );
    case "unavailable":
      return (
        <AccountProblem
          title="Schedence can't load your account right now"
          actions={
            <Link
              href="/"
              prefetch={false}
              className="inline-flex h-9 items-center rounded-lg bg-primary px-3.5 text-sm font-semibold text-white transition-colors hover:bg-primary-strong"
            >
              Try again
            </Link>
          }
        >
          The account service didn&apos;t respond. Try again in a moment. If it keeps happening, contact your Schedence
          administrator.
        </AccountProblem>
      );
  }
}

function Welcome({ account }: { account: Account }) {
  const firstName = account.fullName.split(/\s+/)[0] || account.fullName;
  const institutions = Map.groupBy(account.roles, (role) => role.institutionId);
  const institutionName = (id: string) => account.institutions.find((institution) => institution.id === id)?.name;

  return (
    <div className="animate-rise-in">
      <h1 className="text-[2rem] font-semibold leading-tight tracking-[-0.02em] text-ink font-stretch-semi-expanded">
        Welcome, {firstName}
      </h1>
      <p className="mt-1.5 text-[15px] text-muted">Signed in as {account.email}</p>

      <section aria-labelledby="your-access" className="mt-8 max-w-2xl rounded-2xl border border-line bg-surface">
        <div className="flex items-center justify-between gap-4 border-b border-line px-5 py-4">
          <h2 id="your-access" className="text-sm font-semibold text-ink">
            Your access
          </h2>
          <span className="rounded-full bg-accent px-2.5 py-0.5 text-xs font-semibold text-accent-ink">
            {account.accountStatus === "ACTIVE" ? "Active" : "Disabled"}
          </span>
        </div>

        {account.roles.length === 0 ? (
          <p className="px-5 py-4 text-sm text-muted">
            No roles are assigned to you yet. Your Schedence administrator can add them.
          </p>
        ) : (
          [...institutions].map(([institutionId, roles]) => (
            <div key={institutionId} className="px-5 py-2">
              {institutions.size > 1 && (
                <p className="pt-2 font-mono text-[11px] uppercase tracking-wider text-secondary">
                  {institutionName(institutionId) ?? `Institution ${institutionId.slice(0, 8)}`}
                </p>
              )}
              <ul>
                {roles.map((role) => (
                  <li
                    key={role.roleCode}
                    className="flex items-center justify-between gap-4 border-b border-line py-3 last:border-b-0"
                  >
                    <span className="rounded-md bg-primary-soft px-2 py-1 text-[13px] font-semibold text-primary-strong">
                      {role.roleName}
                    </span>
                    <span className="text-[13px] text-muted">
                      Assigned {assignedDate.format(new Date(role.assignedAt))}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </section>
    </div>
  );
}

function AccountProblem({ title, children, actions }: { title: string; children: ReactNode; actions: ReactNode }) {
  return (
    <section className="mx-auto grid max-w-4xl animate-rise-in items-center gap-8 rounded-[28px] border border-line bg-surface p-6 sm:p-10 md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <Image
        src="/Reminders-rafiki.svg"
        alt=""
        width={500}
        height={500}
        className="mx-auto h-auto w-full max-w-[16rem] md:max-w-xs"
      />
      <div>
        <h1 className="text-2xl font-semibold leading-tight tracking-[-0.02em] text-ink font-stretch-semi-expanded">
          {title}
        </h1>
        <p className="mt-3 text-[15px] leading-6 text-muted">{children}</p>
        <div className="mt-6 flex flex-wrap gap-3">{actions}</div>
      </div>
    </section>
  );
}

function AccountSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading your account" className="animate-pulse">
      <div className="h-9 w-64 rounded-lg bg-line/70" />
      <div className="mt-3 h-4 w-48 rounded bg-line/70" />
      <div className="mt-8 h-40 max-w-2xl rounded-2xl bg-line/50" />
    </div>
  );
}
