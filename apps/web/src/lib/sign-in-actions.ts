"use server";

import { unstable_rethrow } from "next/navigation";
import QRCode from "qrcode";
import { signIn } from "@/auth";
import {
  type AuthStep,
  answerChallenge,
  finishAuthenticatorSetup,
  resetPassword,
  sendPasswordResetCode,
  signInWithPassword,
  startAuthenticatorSetup,
} from "@/lib/cognito";
import { safeRedirectPath } from "@/lib/safe-redirect";
import {
  clearPendingChallenge,
  createHandoff,
  type PendingChallenge,
  readPendingChallenge,
  savePendingChallenge,
} from "@/lib/sign-in-session";

export type SignInStep =
  | "password"
  | "new-password"
  | "setup-authenticator"
  | "authenticator"
  | "forgot-password"
  | "reset-password";

/** What the sign-in form shows next. Never holds Cognito's session; that stays in an HTTP-only cookie. */
export type SignInState = {
  step: SignInStep;
  email?: string;
  error?: string;
  /** A neutral or success message, such as "Password reset". */
  notice?: string;
  /** Only on the setup-authenticator step. */
  authenticator?: { qrSvg: string; secret: string };
  /** Only on the reset-password step: the masked address the code went to. */
  codeDestination?: string;
};

const MESSAGES = {
  wrongPassword: "Incorrect email or password.",
  tooManyAttempts: "Too many attempts. Wait a few minutes, then try again.",
  timedOut: "Your sign-in timed out. Enter your password again to continue.",
  wrongCode: "That code didn't match. Check your authenticator app and try again.",
  configuration: "Sign-in isn't set up correctly on this server. Contact your Schedence administrator.",
  generic: "Something went wrong. Try again.",
};

/** Handles every sign-in form. The submit button's `intent` says which step the user answered. */
export async function signInStep(previous: SignInState, formData: FormData): Promise<SignInState> {
  try {
    const result = await handle(previous, formData);
    if ("step" in result) return result;

    await clearPendingChallenge();
    const handoff = await createHandoff(result.tokens);
    // Throws Next.js's redirect to the page the user asked for; unstable_rethrow lets it through.
    await signIn("credentials", { handoff, redirectTo: safeRedirectPath(formData.get("callbackUrl")) });
    return { step: "password" };
  } catch (error) {
    unstable_rethrow(error);
    return failure(previous, error);
  }
}

type SignedIn = Extract<AuthStep, { type: "signed-in" }>;

async function handle(previous: SignInState, formData: FormData): Promise<SignInState | SignedIn> {
  const field = (name: string) => {
    const value = formData.get(name);
    return typeof value === "string" ? value : "";
  };
  const email = field("email").trim() || previous.email || "";

  switch (field("intent")) {
    case "sign-in": {
      const password = field("password");
      if (!email || !password) return { step: "password", email, error: "Enter your email address and password." };
      return next(await signInWithPassword(email, password), email);
    }

    case "new-password": {
      const pending = await readPendingChallenge("NEW_PASSWORD_REQUIRED");
      if (!pending) return timedOut(email);
      const password = field("password");
      if (password !== field("confirmPassword")) {
        return { ...previous, error: "The passwords don't match." };
      }
      return next(await answerChallenge("NEW_PASSWORD_REQUIRED", pending, { NEW_PASSWORD: password }), pending.email);
    }

    case "verify-authenticator-setup": {
      const pending = await readPendingChallenge("MFA_SETUP");
      if (!pending) return timedOut(email);
      const code = field("code").replace(/\s/g, "");
      if (!/^\d{6}$/.test(code)) return { ...previous, error: "Enter the 6-digit code from your authenticator app." };
      return next(await finishAuthenticatorSetup(pending, code), pending.email);
    }

    case "authenticator-code": {
      const pending = await readPendingChallenge("SOFTWARE_TOKEN_MFA");
      if (!pending) return timedOut(email);
      const code = field("code").replace(/\s/g, "");
      if (!/^\d{6}$/.test(code)) return { ...previous, error: "Enter the 6-digit code from your authenticator app." };
      return next(
        await answerChallenge("SOFTWARE_TOKEN_MFA", pending, { SOFTWARE_TOKEN_MFA_CODE: code }),
        pending.email,
      );
    }

    case "forgot-password":
      await clearPendingChallenge();
      return { step: "forgot-password", email };

    case "send-reset-code": {
      if (!email) return { step: "forgot-password", error: "Enter your email address." };
      const destination = await sendPasswordResetCode(email);
      return { step: "reset-password", email, codeDestination: destination };
    }

    case "reset-password": {
      const code = field("code").replace(/\s/g, "");
      const password = field("password");
      if (!code) return { ...previous, error: "Enter the code from the email." };
      if (password !== field("confirmPassword")) return { ...previous, error: "The passwords don't match." };
      await resetPassword(email, code, password);
      return { step: "password", email, notice: "Your password has been reset. Sign in with your new password." };
    }

    case "restart":
      await clearPendingChallenge();
      return { step: "password", email };
  }
  return { ...previous, error: MESSAGES.generic };
}

/** Saves a new challenge for the next form and says which step to show; tokens pass straight through. */
async function next(step: AuthStep, email: string): Promise<SignInState | SignedIn> {
  if (step.type === "signed-in") return step;

  const pending: PendingChallenge = { challenge: step.challenge, session: step.session, username: step.username, email };
  switch (step.challenge) {
    case "NEW_PASSWORD_REQUIRED":
      await savePendingChallenge(pending);
      return { step: "new-password", email };
    case "SOFTWARE_TOKEN_MFA":
      await savePendingChallenge(pending);
      return { step: "authenticator", email };
    case "MFA_SETUP": {
      const setup = await startAuthenticatorSetup(step.session);
      await savePendingChallenge({ ...pending, session: setup.session });
      const uri = `otpauth://totp/Schedence:${encodeURIComponent(email)}?secret=${setup.secret}&issuer=Schedence`;
      const qrSvg = await QRCode.toString(uri, { type: "svg", margin: 0, errorCorrectionLevel: "M" });
      return { step: "setup-authenticator", email, authenticator: { qrSvg, secret: setup.secret } };
    }
  }
}

async function timedOut(email: string): Promise<SignInState> {
  await clearPendingChallenge();
  return { step: "password", email, error: MESSAGES.timedOut };
}

/** Turns a Cognito error into a message on the same step. Never logs passwords or codes, only the error name. */
async function failure(previous: SignInState, error: unknown): Promise<SignInState> {
  const name = error instanceof Error ? error.name : "UnknownError";
  const message = error instanceof Error ? error.message : "";
  const stay = (text: string): SignInState => ({ ...previous, notice: undefined, error: text });

  switch (name) {
    case "NotAuthorizedException":
      if (/secret hash/i.test(message)) break;
      if (/session is expired|invalid session/i.test(message)) return timedOut(previous.email ?? "");
      if (/attempts exceeded/i.test(message)) return stay(MESSAGES.tooManyAttempts);
      if (/temporary password has expired/i.test(message)) {
        return stay("Your invitation has expired. Ask your Schedence administrator to send a new one.");
      }
      if (/disabled/i.test(message)) return stay("This account is disabled. Contact your Schedence administrator.");
      if (/cannot be reset in the current state/i.test(message)) {
        return stay(
          "This account hasn't been activated yet. Sign in with the temporary password from your invitation email.",
        );
      }
      if (previous.step === "password") return stay(MESSAGES.wrongPassword);
      break;
    case "UserNotFoundException":
      return stay(previous.step === "password" ? MESSAGES.wrongPassword : MESSAGES.generic);
    case "PasswordResetRequiredException":
      return {
        step: "forgot-password",
        email: previous.email,
        notice: "You need to reset your password before you can sign in. We'll email you a code.",
      };
    case "InvalidPasswordException":
      // Cognito's text names the rule that failed, e.g. "Password does not conform to policy: Password must have numeric characters".
      return stay(message.split(": ").at(-1)?.replace(/\.?$/, ".") ?? "That password isn't allowed.");
    case "CodeMismatchException":
    case "AuthenticatorCodeError":
    case "EnableSoftwareTokenMFAException":
      return stay(previous.step === "reset-password" ? "That code didn't match. Check the email and try again." : MESSAGES.wrongCode);
    case "ExpiredCodeException":
      return stay(
        previous.step === "reset-password"
          ? "That code has expired. Send a new one."
          : "That code has expired. Wait for a new code in your authenticator app.",
      );
    case "LimitExceededException":
    case "TooManyRequestsException":
    case "TooManyFailedAttemptsException":
      return stay(MESSAGES.tooManyAttempts);
    case "UnsupportedChallengeError":
      console.warn("[auth] Sign-in stopped:", message);
      return stay("Your account needs a sign-in step Schedence doesn't support. Contact your Schedence administrator.");
    case "CognitoConfigError":
    case "ResourceNotFoundException":
      console.error("[auth] Sign-in is misconfigured:", name, message);
      return stay(MESSAGES.configuration);
    case "InvalidParameterException":
      if (/no registered\/verified email|verified email/i.test(message)) {
        return stay("Your account has no verified email address. Ask your Schedence administrator to reset your password.");
      }
      if (/flow not enabled/i.test(message)) {
        console.error("[auth] Enable ALLOW_USER_PASSWORD_AUTH on the Schedence-Web app client.");
        return stay(MESSAGES.configuration);
      }
      break;
  }
  // Cognito's messages never contain the password or code, so they are safe to log.
  console.error("[auth] Sign-in step failed:", name, message);
  return stay(/secret hash/i.test(message) ? MESSAGES.configuration : MESSAGES.generic);
}
