import type { Metadata } from "next";
import { Suspense } from "react";
import { type LoginSearchParams, readLoginState } from "./login-state";
import { LoginShowcase } from "./showcase";
import { SignInFlow } from "./sign-in-flow";

export const metadata: Metadata = { title: "Sign in" };

const FIRST_SIGN_IN_STEPS = [
  "Open the invitation email from Schedence. Its temporary password works for 7 days.",
  "Sign in here with that password, then choose a new one.",
  "Scan the QR code with an authenticator app, such as Microsoft Authenticator or Google Authenticator.",
];

export default function LoginPage({ searchParams }: PageProps<"/login">) {
  return (
    <main className="min-h-dvh lg:grid lg:grid-cols-2">
      <LoginShowcase />

      <section className="flex flex-col px-4 pb-6 pt-6 sm:px-8 lg:px-12 lg:py-10">
        <div className="mx-auto w-full max-w-[25rem] lg:my-auto">
          <Suspense
            fallback={
              <SignInFlow callbackUrl="/">
                <FirstSignInHelp />
              </SignInFlow>
            }
          >
            <SignInSection searchParams={searchParams} />
          </Suspense>
        </div>

        <p className="mx-auto mt-10 w-full max-w-[25rem] text-xs text-muted">
          Illustrations by{" "}
          <a
            href="https://storyset.com"
            target="_blank"
            rel="noopener noreferrer"
            className="underline decoration-line underline-offset-2 hover:text-primary-strong"
          >
            Storyset
          </a>
        </p>
      </section>
    </main>
  );
}

async function SignInSection({ searchParams }: { searchParams: LoginSearchParams }) {
  const { errorMessage, callbackUrl } = await readLoginState(searchParams);
  return (
    <SignInFlow callbackUrl={callbackUrl} initialError={errorMessage}>
      <FirstSignInHelp />
    </SignInFlow>
  );
}

function FirstSignInHelp() {
  return (
    <>
      <section aria-labelledby="first-sign-in" className="mt-8 rounded-2xl bg-accent p-5">
        <h2 id="first-sign-in" className="text-sm font-semibold text-ink">
          First time signing in?
        </h2>
        <ol className="mt-3 space-y-2.5">
          {FIRST_SIGN_IN_STEPS.map((step, index) => (
            <li key={step} className="flex gap-3 text-sm leading-5 text-ink/85">
              <span className="mt-px grid size-5 shrink-0 place-items-center rounded-full bg-surface font-mono text-[11px] font-medium text-accent-ink">
                {index + 1}
              </span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
      </section>

      <dl className="mt-6 space-y-3 text-[13px] leading-5">
        <div>
          <dt className="font-semibold text-ink">Invitation expired?</dt>
          <dd className="text-muted">Ask your Schedence administrator to send a new one.</dd>
        </div>
        <div>
          <dt className="font-semibold text-ink">No account?</dt>
          <dd className="text-muted">Your institution&apos;s Schedence administrator creates accounts.</dd>
        </div>
      </dl>
    </>
  );
}
