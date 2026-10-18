import "next-auth";
import "next-auth/jwt";

declare module "next-auth" {
  /** What the Credentials provider in src/auth.ts returns: the user plus Cognito's tokens. */
  interface User {
    accessToken?: string;
    refreshToken?: string;
    /** Access token expiry, in seconds since the epoch. */
    expiresAt?: number;
  }

  interface Session {
    /** Cognito access token. Server-side only: removed from the /api/auth/session response. */
    accessToken?: string;
    error?: "RefreshTokenError";
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accessToken?: string;
    refreshToken?: string;
    /** Access token expiry, in seconds since the epoch. */
    expiresAt?: number;
    error?: "RefreshTokenError";
  }
}
