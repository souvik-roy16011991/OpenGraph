import { NextRequest, NextResponse } from "next/server";

/**
 * Auth gate — always on.
 *
 * Until the ``auth_token`` cookie is present (written by ``lib/auth.ts`` on
 * signup/login) the visitor only sees the sign-in / sign-up pages.
 *
 * We intentionally do NOT verify the JWT here — the middleware runs on the
 * edge without access to the signing secret. Presence is sufficient for a
 * redirect; the backend verifies the signature on every protected request,
 * which is where auth actually lives.
 */

const PUBLIC_PREFIXES = [
  "/sign-in",
  "/sign-up",
  "/_next",
  "/favicon",
  "/opengraph-mark.svg",
];

function isPublic(pathname: string): boolean {
  return PUBLIC_PREFIXES.some((p) => pathname.startsWith(p));
}

function hasSession(req: NextRequest): boolean {
  return Boolean(req.cookies.get("auth_token")?.value);
}

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;

  if (isPublic(pathname)) return NextResponse.next();
  if (hasSession(req)) return NextResponse.next();

  const signIn = req.nextUrl.clone();
  signIn.pathname = "/sign-in";
  signIn.search = "";
  signIn.searchParams.set("return_to", pathname + (search || ""));
  return NextResponse.redirect(signIn);
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
