import "server-only";
import { cookies } from "next/headers";
import { decode, encode } from "next-auth/jwt";
import { type ChallengeContext, CognitoConfigError, type CognitoTokens, type SupportedChallenge } from "@/lib/cognito";

// Both values are encrypted with AUTH_SECRET, like the Auth.js session cookie, each under its own salt.

const CHALLENGE_COOKIE = "schedence-sign-in";
/** Cognito's sign-in session lasts 3 to 15 minutes, depending on the app client; the cookie never outlives that. */
const CHALLENGE_MAX_AGE = 15 * 60;
const HANDOFF_SALT = "schedence-sign-in-handoff";
const HANDOFF_MAX_AGE = 60;

/** A sign-in that is waiting for the user's next answer, kept in an HTTP-only cookie between steps. */
export type PendingChallenge = ChallengeContext & { challenge: SupportedChallenge; email: string };

/** What the Credentials provider in src/auth.ts turns into the session. */
export type SignInHandoff = {
  sub: string;
  email?: string;
  name?: string;
  accessToken: string;
  refreshToken?: string;
  /** Access token expiry, in seconds since the epoch. */
  expiresAt: number;
};

function secret(): string {
  const value = process.env.AUTH_SECRET;
  if (!value) throw new CognitoConfigError("AUTH_SECRET is required.");
  return value;
}

export async function savePendingChallenge(pending: PendingChallenge): Promise<void> {
  const value = await encode<PendingChallenge>({
    token: pending,
    secret: secret(),
    salt: CHALLENGE_COOKIE,
    maxAge: CHALLENGE_MAX_AGE,
  });
  (await cookies()).set(CHALLENGE_COOKIE, value, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/login",
    maxAge: CHALLENGE_MAX_AGE,
  });
}

/** The pending challenge, if there is one and it is the one the form answers; otherwise null. */
export async function readPendingChallenge(challenge: SupportedChallenge): Promise<PendingChallenge | null> {
  const value = (await cookies()).get(CHALLENGE_COOKIE)?.value;
  if (!value) return null;
  try {
    const pending = await decode<Partial<PendingChallenge>>({ token: value, secret: secret(), salt: CHALLENGE_COOKIE });
    if (pending?.challenge !== challenge || !pending.session || !pending.username || !pending.email) return null;
    return { challenge, session: pending.session, username: pending.username, email: pending.email };
  } catch {
    return null;
  }
}

export async function clearPendingChallenge(): Promise<void> {
  (await cookies()).delete({ name: CHALLENGE_COOKIE, path: "/login" });
}

/**
 * Seals Cognito's tokens for the Credentials provider. The handoff is minted only here and passed to
 * signIn() in the same server process, so a browser can't forge one and post it to the Auth.js callback.
 */
export async function createHandoff(tokens: CognitoTokens): Promise<string> {
  // Read without verifying: the tokens came straight from Cognito over TLS in this request.
  const claims = jwtClaims(tokens.idToken ?? tokens.accessToken);
  if (typeof claims.sub !== "string") throw new Error("Cognito token has no subject.");
  const handoff: SignInHandoff = {
    sub: claims.sub,
    email: typeof claims.email === "string" ? claims.email : undefined,
    name: typeof claims.name === "string" ? claims.name : undefined,
    accessToken: tokens.accessToken,
    refreshToken: tokens.refreshToken,
    expiresAt: Math.floor(Date.now() / 1000) + tokens.expiresIn,
  };
  return encode<SignInHandoff>({ token: handoff, secret: secret(), salt: HANDOFF_SALT, maxAge: HANDOFF_MAX_AGE });
}

export async function readHandoff(value: unknown): Promise<SignInHandoff | null> {
  if (typeof value !== "string" || !value) return null;
  try {
    const handoff = await decode<Partial<SignInHandoff>>({ token: value, secret: secret(), salt: HANDOFF_SALT });
    if (!handoff?.sub || !handoff.accessToken || typeof handoff.expiresAt !== "number") return null;
    return handoff as SignInHandoff;
  } catch {
    return null;
  }
}

function jwtClaims(token: string): Record<string, unknown> {
  const payload = token.split(".")[1];
  if (!payload) throw new Error("Malformed Cognito token.");
  return JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as Record<string, unknown>;
}
