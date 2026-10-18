"use server";

import { signOut } from "@/auth";

/** Clears the session cookie and revokes Cognito's refresh token (see `events.signOut` in src/auth.ts). */
export async function signOutOfSchedence() {
  await signOut({ redirectTo: "/login" });
}
