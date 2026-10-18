"use client";

import { type ReactNode, useActionState, useState, useTransition } from "react";
import { SubmitButton } from "@/components/submit-button";
import { type SignInState, signInStep } from "@/lib/sign-in-actions";

type Dispatch = (payload: FormData) => void;

const FIELD =
  "block h-12 w-full rounded-xl border border-line bg-surface px-4 text-[15px] text-ink placeholder:text-secondary transition-colors hover:border-secondary/70 focus:border-primary";
const CODE_FIELD = `${FIELD} text-center font-mono text-xl tracking-[0.45em] placeholder:tracking-[0.45em]`;
const LABEL = "mb-1.5 block text-sm font-semibold text-ink";
const PRIMARY =
  "group flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-primary px-5 text-[15px] font-semibold text-white shadow-[0_8px_20px_-10px_var(--color-primary)] transition-colors hover:bg-primary-strong active:bg-primary-strong";
const LINK = "rounded text-sm font-semibold text-primary-strong underline-offset-2 hover:underline disabled:opacity-60";

/**
 * The sign-in form and the steps Cognito can ask for after it. Each form posts to `signInStep`, which
 * answers with the next step to show. `children` (help for first-time users) shows on the first step only.
 */
export function SignInFlow({
  callbackUrl,
  initialError,
  children,
}: {
  callbackUrl: string;
  initialError?: string;
  children: ReactNode;
}) {
  const [state, dispatch] = useActionState(signInStep, { step: "password", error: initialError });
  const props = { state, dispatch, callbackUrl };

  return (
    // Keyed by step so each step starts with empty fields and plays the entrance animation.
    <div key={state.step} className="animate-rise-in">
      {state.step === "password" && <PasswordStep {...props}>{children}</PasswordStep>}
      {state.step === "new-password" && <NewPasswordStep {...props} />}
      {state.step === "setup-authenticator" && <SetupAuthenticatorStep {...props} />}
      {state.step === "authenticator" && <AuthenticatorStep {...props} />}
      {state.step === "forgot-password" && <ForgotPasswordStep {...props} />}
      {state.step === "reset-password" && <ResetPasswordStep {...props} />}
    </div>
  );
}

type StepProps = { state: SignInState; dispatch: Dispatch; callbackUrl: string };

function PasswordStep({ state, dispatch, callbackUrl, children }: StepProps & { children: ReactNode }) {
  return (
    <>
      <Heading title="Sign in">Use the email address your institution registered with Schedence.</Heading>
      <Messages state={state} />
      <form action={dispatch} className="mt-8 space-y-5">
        <Hidden intent="sign-in" callbackUrl={callbackUrl} />
        <div>
          <label htmlFor="email" className={LABEL}>
            Email address
          </label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="username"
            required
            defaultValue={state.email}
            placeholder="name@institution.edu"
            className={FIELD}
          />
        </div>
        <div>
          <div className="mb-1.5 flex items-baseline justify-between gap-4">
            <label htmlFor="password" className="text-sm font-semibold text-ink">
              Password
            </label>
            <StepLink intent="forgot-password" dispatch={dispatch}>
              Forgot password?
            </StepLink>
          </div>
          <PasswordField id="password" autoComplete="current-password" />
        </div>
        <PrimaryButton pendingLabel="Signing in…">Sign in</PrimaryButton>
      </form>
      {children}
    </>
  );
}

function NewPasswordStep({ state, dispatch, callbackUrl }: StepProps) {
  return (
    <>
      <Heading title="Choose a new password">
        You signed in with a temporary password. Choose your own password for{" "}
        <span className="font-semibold text-ink">{state.email}</span>.
      </Heading>
      <Messages state={state} />
      <form action={dispatch} className="mt-8 space-y-5">
        <Hidden intent="new-password" callbackUrl={callbackUrl} email={state.email} />
        <div>
          <label htmlFor="password" className={LABEL}>
            New password
          </label>
          <PasswordField id="password" autoComplete="new-password" describedBy="password-rules" autoFocus />
          <p id="password-rules" className="mt-2 text-[13px] leading-5 text-muted">
            At least 8 characters, with upper and lower case letters, a number and a symbol.
          </p>
        </div>
        <div>
          <label htmlFor="confirm-password" className={LABEL}>
            Confirm new password
          </label>
          <PasswordField id="confirm-password" name="confirmPassword" autoComplete="new-password" />
        </div>
        <PrimaryButton pendingLabel="Saving…">Save and continue</PrimaryButton>
      </form>
      <BackLink dispatch={dispatch} email={state.email}>
        Cancel and go back to sign in
      </BackLink>
    </>
  );
}

function SetupAuthenticatorStep({ state, dispatch, callbackUrl }: StepProps) {
  const { qrSvg = "", secret = "" } = state.authenticator ?? {};
  return (
    <>
      <Heading title="Set up your authenticator">
        Each time you sign in, Schedence asks for a code from an authenticator app on your phone.
      </Heading>
      <Messages state={state} />
      <ol className="mt-8 space-y-7">
        <SetupStep number={1} title="Install an authenticator app">
          Such as Microsoft Authenticator or Google Authenticator.
        </SetupStep>
        <SetupStep number={2} title="Scan this QR code with the app">
          <div className="mt-3 flex flex-col gap-4 sm:flex-row sm:items-start">
            <div
              role="img"
              aria-label="QR code for adding Schedence to your authenticator app"
              className="size-40 shrink-0 rounded-2xl border border-line bg-white p-3 [&_svg]:size-full"
              // Generated on the server by the qrcode package from Cognito's secret; contains no user input markup.
              dangerouslySetInnerHTML={{ __html: qrSvg }}
            />
            <div className="min-w-0">
              <p>Can&apos;t scan it? Enter this key in the app instead.</p>
              <p className="mt-2 rounded-lg bg-secondary-soft px-3 py-2 font-mono text-[13px] leading-5 text-ink">
                {secret.match(/.{1,4}/g)?.join(" ")}
              </p>
              <CopyButton value={secret} />
            </div>
          </div>
        </SetupStep>
        <SetupStep number={3} title="Enter the 6-digit code the app shows">
          <form action={dispatch} className="mt-3 space-y-4">
            <Hidden intent="verify-authenticator-setup" callbackUrl={callbackUrl} email={state.email} />
            <label htmlFor="code" className="sr-only">
              6-digit code
            </label>
            <CodeField />
            <PrimaryButton pendingLabel="Verifying…">Verify and sign in</PrimaryButton>
          </form>
        </SetupStep>
      </ol>
      <BackLink dispatch={dispatch} email={state.email}>
        Cancel and go back to sign in
      </BackLink>
    </>
  );
}

function AuthenticatorStep({ state, dispatch, callbackUrl }: StepProps) {
  return (
    <>
      <Heading title="Enter your code">
        Open your authenticator app and enter the 6-digit code for Schedence
        {state.email && (
          <>
            {" "}
            (<span className="font-semibold text-ink">{state.email}</span>)
          </>
        )}
        .
      </Heading>
      <Messages state={state} />
      <form action={dispatch} className="mt-8 space-y-5">
        <Hidden intent="authenticator-code" callbackUrl={callbackUrl} email={state.email} />
        <div>
          <label htmlFor="code" className={LABEL}>
            Authenticator code
          </label>
          <CodeField autoFocus />
        </div>
        <PrimaryButton pendingLabel="Verifying…">Verify</PrimaryButton>
      </form>
      <BackLink dispatch={dispatch} email={state.email}>
        Sign in with a different account
      </BackLink>
    </>
  );
}

function ForgotPasswordStep({ state, dispatch, callbackUrl }: StepProps) {
  return (
    <>
      <Heading title="Reset your password">
        Enter the email address you sign in with. We&apos;ll email you a code to reset your password.
      </Heading>
      <Messages state={state} />
      <form action={dispatch} className="mt-8 space-y-5">
        <Hidden intent="send-reset-code" callbackUrl={callbackUrl} />
        <div>
          <label htmlFor="email" className={LABEL}>
            Email address
          </label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="username"
            required
            autoFocus
            defaultValue={state.email}
            placeholder="name@institution.edu"
            className={FIELD}
          />
        </div>
        <PrimaryButton pendingLabel="Sending…">Send reset code</PrimaryButton>
      </form>
      <BackLink dispatch={dispatch} email={state.email}>
        Back to sign in
      </BackLink>
    </>
  );
}

function ResetPasswordStep({ state, dispatch, callbackUrl }: StepProps) {
  return (
    <>
      <Heading title="Check your email">
        {state.codeDestination ? (
          <>
            We sent a code to <span className="font-semibold text-ink">{state.codeDestination}</span>.
          </>
        ) : (
          <>If an account exists for that address, we sent it a code.</>
        )}{" "}
        Enter it below with your new password.
      </Heading>
      <Messages state={state} />
      <form action={dispatch} className="mt-8 space-y-5">
        <Hidden intent="reset-password" callbackUrl={callbackUrl} email={state.email} />
        <div>
          <div className="mb-1.5 flex items-baseline justify-between gap-4">
            <label htmlFor="code" className="text-sm font-semibold text-ink">
              Code from the email
            </label>
            <StepLink intent="send-reset-code" dispatch={dispatch} email={state.email}>
              Send a new code
            </StepLink>
          </div>
          <CodeField autoFocus />
        </div>
        <div>
          <label htmlFor="password" className={LABEL}>
            New password
          </label>
          <PasswordField id="password" autoComplete="new-password" describedBy="password-rules" />
          <p id="password-rules" className="mt-2 text-[13px] leading-5 text-muted">
            At least 8 characters, with upper and lower case letters, a number and a symbol.
          </p>
        </div>
        <div>
          <label htmlFor="confirm-password" className={LABEL}>
            Confirm new password
          </label>
          <PasswordField id="confirm-password" name="confirmPassword" autoComplete="new-password" />
        </div>
        <PrimaryButton pendingLabel="Resetting…">Reset password</PrimaryButton>
      </form>
      <BackLink dispatch={dispatch} email={state.email}>
        Back to sign in
      </BackLink>
    </>
  );
}

function Heading({ title, children }: { title: string; children: ReactNode }) {
  return (
    <>
      <h1 className="text-[2rem] font-semibold leading-tight tracking-[-0.02em] text-ink font-stretch-semi-expanded">
        {title}
      </h1>
      <p className="mt-2 text-[15px] leading-6 text-muted">{children}</p>
    </>
  );
}

function Messages({ state }: { state: SignInState }) {
  return (
    <>
      {state.error && (
        <div
          role="alert"
          className="mt-6 flex gap-3 rounded-xl border border-danger/15 bg-danger-soft px-4 py-3 text-sm leading-5 text-danger"
        >
          <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="mt-px size-4 shrink-0">
            <path
              fillRule="evenodd"
              d="M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Zm-8-5a.75.75 0 0 1 .75.75v4.5a.75.75 0 0 1-1.5 0v-4.5A.75.75 0 0 1 10 5Zm0 10a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z"
              clipRule="evenodd"
            />
          </svg>
          <p>{state.error}</p>
        </div>
      )}
      {state.notice && (
        <div
          role="status"
          className="mt-6 flex gap-3 rounded-xl border border-accent-ink/15 bg-accent px-4 py-3 text-sm leading-5 text-accent-ink"
        >
          <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="mt-px size-4 shrink-0">
            <path
              fillRule="evenodd"
              d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm3.86-9.81a.75.75 0 0 0-1.22-.88l-3.24 4.48-1.61-1.6a.75.75 0 1 0-1.06 1.06l2.25 2.25a.75.75 0 0 0 1.14-.09l3.74-5.22Z"
              clipRule="evenodd"
            />
          </svg>
          <p>{state.notice}</p>
        </div>
      )}
    </>
  );
}

/** Which step the form answers, where to go after sign-in, and the email for steps that no longer ask for it. */
function Hidden({ intent, callbackUrl, email }: { intent: string; callbackUrl: string; email?: string }) {
  return (
    <>
      <input type="hidden" name="intent" value={intent} />
      <input type="hidden" name="callbackUrl" value={callbackUrl} />
      {email !== undefined && <input type="hidden" name="email" value={email} />}
    </>
  );
}

function PrimaryButton({ pendingLabel, children }: { pendingLabel: string; children: ReactNode }) {
  return (
    <SubmitButton pendingLabel={pendingLabel} className={PRIMARY}>
      {children}
      <svg
        viewBox="0 0 20 20"
        fill="currentColor"
        aria-hidden="true"
        className="size-4 transition-transform group-hover:translate-x-0.5"
      >
        <path
          fillRule="evenodd"
          d="M3 10a.75.75 0 0 1 .75-.75h10.64l-3.22-3.22a.75.75 0 1 1 1.06-1.06l4.5 4.5a.75.75 0 0 1 0 1.06l-4.5 4.5a.75.75 0 1 1-1.06-1.06l3.22-3.22H3.75A.75.75 0 0 1 3 10Z"
          clipRule="evenodd"
        />
      </svg>
    </SubmitButton>
  );
}

function PasswordField({
  id,
  name = "password",
  autoComplete,
  describedBy,
  autoFocus,
}: {
  id: string;
  name?: string;
  autoComplete: "current-password" | "new-password";
  describedBy?: string;
  autoFocus?: boolean;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <input
        id={id}
        name={name}
        type={visible ? "text" : "password"}
        autoComplete={autoComplete}
        required
        autoFocus={autoFocus}
        aria-describedby={describedBy}
        className={`${FIELD} pr-12`}
      />
      <button
        type="button"
        onClick={() => setVisible((value) => !value)}
        aria-label={visible ? "Hide password" : "Show password"}
        aria-controls={id}
        className="absolute inset-y-0 right-0 grid w-12 place-items-center rounded-r-xl text-muted transition-colors hover:text-ink"
      >
        <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="size-4.5">
          {visible ? (
            <path
              fillRule="evenodd"
              d="M3.28 2.22a.75.75 0 0 0-1.06 1.06l14.5 14.5a.75.75 0 1 0 1.06-1.06l-1.75-1.75a10.05 10.05 0 0 0 3.3-4.38 1.65 1.65 0 0 0 0-1.18A10 10 0 0 0 10 3c-1.5 0-2.92.33-4.2.92L3.28 2.22Zm4.02 4.02 1.13 1.13a2.75 2.75 0 0 1 3.7 3.7l1.13 1.13a4.25 4.25 0 0 0-5.96-5.96ZM10.75 16.97l-2.2-2.2a4.25 4.25 0 0 1-3.32-3.32L2.62 8.84a10 10 0 0 0-1.29 2.32 1.65 1.65 0 0 0 0 1.18A10 10 0 0 0 10 17c.25 0 .5 0 .75-.03Z"
              clipRule="evenodd"
            />
          ) : (
            <>
              <path d="M10 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z" />
              <path
                fillRule="evenodd"
                d="M.66 10.59a1.65 1.65 0 0 1 0-1.19A10 10 0 0 1 10 3c4.26 0 7.9 2.66 9.34 6.41.15.38.15.8 0 1.19A10 10 0 0 1 10 17c-4.26 0-7.9-2.66-9.34-6.41ZM14 10a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z"
                clipRule="evenodd"
              />
            </>
          )}
        </svg>
      </button>
    </div>
  );
}

function CodeField({ autoFocus }: { autoFocus?: boolean }) {
  return (
    <input
      id="code"
      name="code"
      type="text"
      inputMode="numeric"
      autoComplete="one-time-code"
      pattern="[0-9 ]*"
      maxLength={7}
      required
      autoFocus={autoFocus}
      placeholder="000000"
      className={CODE_FIELD}
    />
  );
}

/**
 * A text button that switches step without submitting the form it sits in, so Enter still submits the
 * form's main action. It sends only the email (typed so far, or the one already known), never a password.
 */
function StepLink({
  intent,
  dispatch,
  email,
  children,
}: {
  intent: string;
  dispatch: Dispatch;
  email?: string;
  children: ReactNode;
}) {
  const [pending, startTransition] = useTransition();
  return (
    <button
      type="button"
      disabled={pending}
      onClick={(event) => {
        const form = event.currentTarget.form;
        const typed = form ? new FormData(form).get("email") : null;
        const data = new FormData();
        data.set("intent", intent);
        data.set("email", typeof typed === "string" && typed ? typed : (email ?? ""));
        startTransition(() => dispatch(data));
      }}
      className={LINK}
    >
      {children}
    </button>
  );
}

function BackLink({ dispatch, email, children }: { dispatch: Dispatch; email?: string; children: ReactNode }) {
  return (
    <p className="mt-6 flex items-center gap-1.5 text-sm">
      <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="size-4 text-primary-strong">
        <path
          fillRule="evenodd"
          d="M17 10a.75.75 0 0 1-.75.75H5.61l3.22 3.22a.75.75 0 1 1-1.06 1.06l-4.5-4.5a.75.75 0 0 1 0-1.06l4.5-4.5a.75.75 0 0 1 1.06 1.06L5.61 9.25h10.64A.75.75 0 0 1 17 10Z"
          clipRule="evenodd"
        />
      </svg>
      <StepLink intent="restart" dispatch={dispatch} email={email}>
        {children}
      </StepLink>
    </p>
  );
}

function SetupStep({ number, title, children }: { number: number; title: string; children: ReactNode }) {
  return (
    <li className="flex gap-4">
      <span className="grid size-7 shrink-0 place-items-center rounded-full bg-primary-soft font-mono text-xs font-semibold text-primary-strong">
        {number}
      </span>
      <div className="min-w-0 flex-1 pt-0.5 text-sm leading-5 text-muted">
        <p className="font-semibold text-ink">{title}</p>
        <div className="mt-1">{children}</div>
      </div>
    </li>
  );
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        } catch {
          // Clipboard blocked: the key is still selectable on screen.
        }
      }}
      className={`${LINK} mt-2`}
    >
      <span aria-live="polite">{copied ? "Copied" : "Copy key"}</span>
    </button>
  );
}
