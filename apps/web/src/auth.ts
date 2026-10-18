import "server-only";
import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import { refreshCognitoTokens, revokeRefreshToken } from "@/lib/cognito";
import { safeRedirectPath } from "@/lib/safe-redirect";
import { readHandoff } from "@/lib/sign-in-session";

/** Refresh this many seconds before the access token expires. */
const EXPIRY_SKEW_SECONDS = 60;

// The sign-in form on /login talks to Cognito (src/lib/sign-in-actions.ts). When Cognito returns tokens, that
// server action passes them here as a sealed handoff; the Credentials provider only unseals it.
export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Credentials({
      credentials: { handoff: {} },
      async authorize({ handoff }) {
        const sealed = await readHandoff(handoff);
        if (!sealed) return null;
        const { sub, email, name, accessToken, refreshToken, expiresAt } = sealed;
        return { id: sub, email, name, accessToken, refreshToken, expiresAt };
      },
    }),
  ],
  session: { strategy: "jwt" },
  pages: { signIn: "/login", error: "/login" },
  callbacks: {
    // Optimistic check for src/proxy.ts. Pages still verify the account with the identity service.
    authorized({ auth: session, request }) {
      const { pathname, search, searchParams } = request.nextUrl;
      const signedIn = Boolean(session?.user) && !session?.error;

      if (pathname === "/login") {
        // Stay on /login while it shows an error, so a token the API rejected can't cause a redirect loop.
        if (signedIn && !searchParams.has("error")) {
          return Response.redirect(new URL(safeRedirectPath(searchParams.get("callbackUrl")), request.nextUrl));
        }
        return true;
      }
      if (signedIn) return true;

      const login = new URL("/login", request.nextUrl);
      if (pathname !== "/") login.searchParams.set("callbackUrl", `${pathname}${search}`);
      if (session?.error) login.searchParams.set("error", "SessionExpired");
      return Response.redirect(login);
    },

    // Tokens live only in the encrypted session cookie. The ID token is not kept, to keep the cookie small.
    async jwt({ token, user, account }) {
      if (account && user) {
        return {
          ...token,
          accessToken: user.accessToken,
          refreshToken: user.refreshToken,
          expiresAt: user.expiresAt,
          error: undefined,
        };
      }
      if (token.expiresAt && Date.now() < (token.expiresAt - EXPIRY_SKEW_SECONDS) * 1000) {
        return token;
      }
      if (!token.refreshToken) {
        return { ...token, error: "RefreshTokenError" };
      }
      try {
        const refreshed = await refreshCognitoTokens(token.refreshToken);
        return {
          ...token,
          accessToken: refreshed.accessToken,
          refreshToken: refreshed.refreshToken ?? token.refreshToken,
          expiresAt: Math.floor(Date.now() / 1000) + refreshed.expiresIn,
          error: undefined,
        };
      } catch (error) {
        console.warn("[auth] Access token refresh failed:", error instanceof Error ? error.message : "unknown error");
        return { ...token, error: "RefreshTokenError" };
      }
    },

    // The access token is for server code only; the /api/auth/session route strips it before it reaches the browser.
    session({ session, token }) {
      session.accessToken = token.accessToken;
      session.error = token.error;
      return session;
    },
  },
  events: {
    // Best effort: signing out still clears the cookie if Cognito can't be reached.
    async signOut(message) {
      const refreshToken = "token" in message ? message.token?.refreshToken : undefined;
      if (!refreshToken) return;
      try {
        await revokeRefreshToken(refreshToken);
      } catch (error) {
        console.warn("[auth] Refresh token revocation failed:", error instanceof Error ? error.name : "unknown error");
      }
    },
  },
});
