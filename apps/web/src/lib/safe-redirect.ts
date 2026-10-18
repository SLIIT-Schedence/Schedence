/**
 * Returns `value` only if it is a same-site path such as "/users?page=2";
 * anything else (absolute URLs, protocol-relative "//host", the login page itself) becomes "/".
 */
export function safeRedirectPath(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) {
    return "/";
  }
  const url = new URL(value, "http://schedence.invalid");
  if (url.origin !== "http://schedence.invalid" || url.pathname === "/login" || url.pathname.startsWith("/login/")) {
    return "/";
  }
  return `${url.pathname}${url.search}${url.hash}`;
}
