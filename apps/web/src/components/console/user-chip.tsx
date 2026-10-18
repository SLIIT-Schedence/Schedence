import { Icon } from "@/components/icon";
import { signOutOfSchedence } from "@/lib/auth-actions";
import { getAccount } from "@/lib/identity";
import { initials } from "./ui";

/** The signed-in person in a console's top bar, with sign-out. `role` is shown under the name. */
export async function UserChip({ role }: { role: string }) {
  const result = await getAccount();
  if (result.status !== "ok") return null;
  const { fullName, email } = result.account;

  return (
    <div className="flex items-center gap-2 sm:gap-3">
      <div className="hidden min-w-0 text-right leading-tight sm:block">
        <p className="max-w-48 truncate text-sm font-semibold text-ink">{fullName}</p>
        <p className="max-w-48 truncate text-xs text-muted" title={email}>
          {role}
        </p>
      </div>
      <span
        aria-hidden="true"
        className="grid size-10 shrink-0 place-items-center rounded-full bg-primary text-[13px] font-semibold text-white ring-4 ring-primary-soft"
      >
        {initials(fullName || email)}
      </span>
      <form action={signOutOfSchedence}>
        <button
          type="submit"
          aria-label="Sign out"
          title="Sign out"
          className="grid size-10 place-items-center rounded-lg text-muted transition-colors hover:bg-danger-soft hover:text-danger"
        >
          <Icon name="logout" />
        </button>
      </form>
    </div>
  );
}

export function UserChipSkeleton() {
  return <div className="h-10 w-10 animate-pulse rounded-full bg-line/70 sm:w-48 sm:rounded-xl" />;
}
