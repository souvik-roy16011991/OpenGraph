import { NextRequest, NextResponse } from "next/server";

/**
 * Auth gate — always on.
 *
 * Until the user has a Neon Auth session cookie (``stack-refresh-*``, set
 * after sign-in; the cookie name stays the same because the SDK is
 * @stackframe/stack pointed at the Neon Auth tenant) they see nothing
 * except the sign-in / sign-up pages and Neon Auth's own handler routes.
 *
 * Anything else on the site is gated: request → redirect to /sign-in,
 * preserving the original path via ?return_to=.
 */

const PUBLIC_PREFIXES = [
  "/handler",                 // Neon Auth flows (sign-in, OAuth, reset)
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
  return cookies.some((c) => c.name.startsWith("stack-refresh"));
}

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;

  if (isPublic(pathname)) return NextResponse.next();
  if (hasSession(req)) return NextResponse.next();

  const signIn = req.nextUrl.clone();
  signIn.pathname = "/sign-in";
  signIn.search = "";
  // Preserve where the user was trying to go so the sign-in page can
  // forward it as Neon Auth's `after_auth_return_to` query param.
  signIn.searchParams.set("return_to", pathname + (search || ""));
  return NextResponse.redirect(signIn);
}

export const config = {
  // Exclude static assets so the middleware doesn't fire on every .js / .css.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
