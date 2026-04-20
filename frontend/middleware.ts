import { NextRequest, NextResponse } from "next/server";

/**
 * Auth gate — always on.
 *
 * Until the user has a session cookie they see **nothing** except the
 * sign-in / sign-up pages and Stack Auth's own handler routes. The two
 * accepted session markers are:
 *
 *   - ``stack-refresh-*`` — Stack Auth's refresh cookie, set after a real
 *     sign-in when the Stack Auth env vars are configured.
 *   - ``og-session=dev``  — dev-mode cookie dropped by the "Continue as
 *     dev user" server action on /sign-in. Only reachable when Stack Auth
 *     is unconfigured; lets local dev work without Stack credentials, but
 *     still requires an explicit sign-in gesture.
 *
 * Anything else on the site is gated: request → redirect to /sign-in,
 * preserving the original path via ?return_to=.
 */

const PUBLIC_PREFIXES = [
  "/handler",                 // Stack Auth flows (sign-in, OAuth, reset)
  "/sign-in",                 // our smart sign-in page
  "/sign-up",                 // our smart sign-up page
  "/_next",                   // Next internals
  "/favicon",                 // favicon.svg / .ico
  "/opengraph-mark.svg",      // brand SVG (public)
];

function isPublic(pathname: string): boolean {
  return PUBLIC_PREFIXES.some((p) => pathname.startsWith(p));
}

function hasSession(req: NextRequest): boolean {
  const cookies = req.cookies.getAll();
  return cookies.some(
    (c) => c.name.startsWith("stack-refresh") || c.name === "og-session",
  );
}

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;

  if (isPublic(pathname)) return NextResponse.next();
  if (hasSession(req)) return NextResponse.next();

  const signIn = req.nextUrl.clone();
  signIn.pathname = "/sign-in";
  signIn.search = "";
  // Preserve where the user was trying to go so the sign-in action can
  // redirect them back after dev-mode login. Stack Auth's own sign-in
  // already supports its own `after_auth_return_to` param, which we don't
  // need to synthesize here.
  signIn.searchParams.set("return_to", pathname + (search || ""));
  return NextResponse.redirect(signIn);
}

export const config = {
  // Exclude static assets so the middleware doesn't fire on every .js / .css.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
