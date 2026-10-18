// Redirects signed-out visitors to /login and signed-in ones away from it (see `authorized` in src/auth.ts).
// It also refreshes an expiring access token and stores it back in the session cookie.
export { auth as proxy } from "@/auth";

export const config = {
  matcher: ["/((?!api/auth|_next/static|_next/image|favicon.ico|icon.svg|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
