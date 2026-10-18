import { signOutOfSchedence } from "@/lib/auth-actions";
import { SubmitButton } from "./submit-button";

const VARIANTS = {
  quiet: "border border-line bg-surface text-ink hover:border-secondary hover:bg-background",
  primary: "bg-primary text-white hover:bg-primary-strong",
};

export function SignOutButton({ variant }: { variant: keyof typeof VARIANTS }) {
  return (
    <form action={signOutOfSchedence}>
      <SubmitButton
        pendingLabel="Signing out…"
        className={`inline-flex h-9 items-center justify-center gap-2 rounded-lg px-3.5 text-sm font-semibold transition-colors ${VARIANTS[variant]}`}
      >
        Sign out
      </SubmitButton>
    </form>
  );
}
