import "server-only";
import { createHmac } from "node:crypto";
import {
  AssociateSoftwareTokenCommand,
  type AuthenticationResultType,
  type ChallengeNameType,
  CognitoIdentityProviderClient,
  ConfirmForgotPasswordCommand,
  ForgotPasswordCommand,
  GetTokensFromRefreshTokenCommand,
  InitiateAuthCommand,
  RespondToAuthChallengeCommand,
  RevokeTokenCommand,
  VerifySoftwareTokenCommand,
} from "@aws-sdk/client-cognito-identity-provider";

// Every call here is a public user pool API: no AWS credentials, only the app client ID and secret.

export type CognitoTokens = {
  accessToken: string;
  /** Not returned on refresh unless refresh-token rotation is enabled on the app client. */
  refreshToken?: string;
  idToken?: string;
  /** Seconds until the access token expires. */
  expiresIn: number;
};

/** The sign-in challenges this app has screens for. */
export type SupportedChallenge = "NEW_PASSWORD_REQUIRED" | "MFA_SETUP" | "SOFTWARE_TOKEN_MFA";

/** Where a sign-in stands after a call: finished, or waiting for the user to answer a challenge. */
export type AuthStep =
  | { type: "signed-in"; tokens: CognitoTokens }
  | { type: "challenge"; challenge: SupportedChallenge; session: string; username: string };

/** A pending challenge: Cognito's session string plus the pool's own username for the user. */
export type ChallengeContext = { session: string; username: string };

/** The server is missing app client settings. */
export class CognitoConfigError extends Error {
  name = "CognitoConfigError";
}

/** Cognito asked for a step this app has no screen for (for example SMS MFA). */
export class UnsupportedChallengeError extends Error {
  name = "UnsupportedChallengeError";
}

/** VerifySoftwareToken answered without SUCCESS. */
export class AuthenticatorCodeError extends Error {
  name = "AuthenticatorCodeError";
}

const TIMEOUT_MS = 10_000;

function appClient() {
  const clientId = process.env.AUTH_COGNITO_ID;
  const clientSecret = process.env.AUTH_COGNITO_SECRET;
  const region = process.env.AUTH_COGNITO_ISSUER?.match(/^https:\/\/cognito-idp\.([a-z0-9-]+)\.amazonaws\.com\//)?.[1];
  if (!clientId || !clientSecret || !region) {
    throw new CognitoConfigError("AUTH_COGNITO_ID, AUTH_COGNITO_SECRET and AUTH_COGNITO_ISSUER are required.");
  }
  return { clientId, clientSecret, region };
}

let client: CognitoIdentityProviderClient | undefined;

function cognito() {
  client ??= new CognitoIdentityProviderClient({ region: appClient().region });
  return client;
}

const options = () => ({ abortSignal: AbortSignal.timeout(TIMEOUT_MS) });

/** Required on every call because Schedence-Web is a confidential client. */
function secretHash(username: string): string {
  const { clientId, clientSecret } = appClient();
  return createHmac("sha256", clientSecret).update(username + clientId).digest("base64");
}

function toTokens(result: AuthenticationResultType): CognitoTokens {
  if (!result.AccessToken || !result.ExpiresIn) {
    throw new Error("Cognito returned no access token.");
  }
  return {
    accessToken: result.AccessToken,
    refreshToken: result.RefreshToken,
    idToken: result.IdToken,
    expiresIn: result.ExpiresIn,
  };
}

async function toAuthStep(
  result: {
    AuthenticationResult?: AuthenticationResultType;
    ChallengeName?: ChallengeNameType;
    ChallengeParameters?: Record<string, string>;
    Session?: string;
  },
  username: string,
): Promise<AuthStep> {
  if (result.AuthenticationResult) {
    return { type: "signed-in", tokens: toTokens(result.AuthenticationResult) };
  }
  const { ChallengeName: challenge, ChallengeParameters: parameters = {}, Session: session } = result;
  if (!challenge || !session) {
    throw new Error("Cognito returned neither tokens nor a challenge.");
  }
  // Later responses need the pool's internal username (a UUID in an email sign-in pool), not the email.
  const context = { session, username: parameters.USER_ID_FOR_SRP || username };

  switch (challenge) {
    case "NEW_PASSWORD_REQUIRED":
    case "SOFTWARE_TOKEN_MFA":
      return { type: "challenge", challenge, ...context };
    case "MFA_SETUP":
      if (!parameters.MFAS_CAN_SETUP?.includes("SOFTWARE_TOKEN_MFA")) break;
      return { type: "challenge", challenge, ...context };
    case "SELECT_MFA_TYPE":
      if (!parameters.MFAS_CAN_CHOOSE?.includes("SOFTWARE_TOKEN_MFA")) break;
      return answerChallenge("SELECT_MFA_TYPE", context, { ANSWER: "SOFTWARE_TOKEN_MFA" });
  }
  throw new UnsupportedChallengeError(`Unsupported Cognito challenge: ${challenge}`);
}

export async function signInWithPassword(email: string, password: string): Promise<AuthStep> {
  const result = await cognito().send(
    new InitiateAuthCommand({
      ClientId: appClient().clientId,
      AuthFlow: "USER_PASSWORD_AUTH",
      AuthParameters: { USERNAME: email, PASSWORD: password, SECRET_HASH: secretHash(email) },
    }),
    options(),
  );
  return toAuthStep(result, email);
}

export async function answerChallenge(
  challenge: ChallengeNameType,
  context: ChallengeContext,
  responses: Record<string, string>,
): Promise<AuthStep> {
  const result = await cognito().send(
    new RespondToAuthChallengeCommand({
      ClientId: appClient().clientId,
      ChallengeName: challenge,
      Session: context.session,
      ChallengeResponses: { USERNAME: context.username, SECRET_HASH: secretHash(context.username), ...responses },
    }),
    options(),
  );
  return toAuthStep(result, context.username);
}

/** Starts authenticator setup: returns the TOTP secret to show and the session for the next call. */
export async function startAuthenticatorSetup(session: string): Promise<{ secret: string; session: string }> {
  const result = await cognito().send(new AssociateSoftwareTokenCommand({ Session: session }), options());
  if (!result.SecretCode || !result.Session) {
    throw new Error("Cognito returned no authenticator secret.");
  }
  return { secret: result.SecretCode, session: result.Session };
}

/** Checks the first code from the new authenticator, then finishes the MFA_SETUP challenge. */
export async function finishAuthenticatorSetup(context: ChallengeContext, code: string): Promise<AuthStep> {
  const result = await cognito().send(
    new VerifySoftwareTokenCommand({ Session: context.session, UserCode: code, FriendlyDeviceName: "Authenticator app" }),
    options(),
  );
  if (result.Status !== "SUCCESS" || !result.Session) {
    throw new AuthenticatorCodeError("The authenticator code was not accepted.");
  }
  return answerChallenge("MFA_SETUP", { ...context, session: result.Session }, {});
}

/** Emails a password reset code. Returns the masked address it went to, when Cognito says. */
export async function sendPasswordResetCode(email: string): Promise<string | undefined> {
  const result = await cognito().send(
    new ForgotPasswordCommand({ ClientId: appClient().clientId, Username: email, SecretHash: secretHash(email) }),
    options(),
  );
  return result.CodeDeliveryDetails?.Destination;
}

export async function resetPassword(email: string, code: string, password: string): Promise<void> {
  await cognito().send(
    new ConfirmForgotPasswordCommand({
      ClientId: appClient().clientId,
      Username: email,
      ConfirmationCode: code,
      Password: password,
      SecretHash: secretHash(email),
    }),
    options(),
  );
}

export async function refreshCognitoTokens(refreshToken: string): Promise<CognitoTokens> {
  const { clientId, clientSecret } = appClient();
  const result = await cognito().send(
    new GetTokensFromRefreshTokenCommand({ ClientId: clientId, ClientSecret: clientSecret, RefreshToken: refreshToken }),
    options(),
  );
  if (!result.AuthenticationResult) {
    throw new Error("Cognito returned no tokens on refresh.");
  }
  return toTokens(result.AuthenticationResult);
}

/** Revokes the refresh token and the access tokens issued from it, so a copied session cookie stops working. */
export async function revokeRefreshToken(refreshToken: string): Promise<void> {
  const { clientId, clientSecret } = appClient();
  await cognito().send(
    new RevokeTokenCommand({ ClientId: clientId, ClientSecret: clientSecret, Token: refreshToken }),
    options(),
  );
}
