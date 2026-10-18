import { auth } from "@/auth";
import { SignOutButton } from "@/components/sign-out-button";

function initials(nameOrEmail: string): string {
  const words = nameOrEmail.split(/[\s@._-]+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : nameOrEmail.slice(0, 2)).toUpperCase();
}

export async function UserMenu() {
  const session = await auth();
  const name = session?.user?.name ?? session?.user?.email ?? "Signed in";
  const email = session?.user?.email;

  return (
    <div className="flex items-center gap-3">
      <span
        aria-hidden="true"
        className="grid size-9 place-items-center rounded-full bg-primary-soft text-[13px] font-semibold text-primary-strong"
      >
        {initials(name)}
      </span>
      <div className="hidden min-w-0 text-right leading-tight sm:block">
        <p className="truncate text-sm font-semibold text-ink">{name}</p>
        {email && email !== name && <p className="truncate text-xs text-muted">{email}</p>}
      </div>
      <SignOutButton variant="quiet" />
    </div>
  );
}
