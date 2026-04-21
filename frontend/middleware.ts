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

const INFRA_PREFIXES = ["/_next", "/favicon", "/opengraph-mark.svg"];
const AUTH_PAGE_PREFIXES = ["/sign-in", "/sign-up"];

function startsWithAny(pathname: string, prefixes: string[]): boolean {
  return prefixes.some((p) => pathname.startsWith(p));
}

function hasSession(req: NextRequest): boolean {
  return Boolean(req.cookies.get("auth_token")?.value);
}

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;

  if (startsWithAny(pathname, INFRA_PREFIXES)) return NextResponse.next();

  const isAuthPage = startsWithAny(pathname, AUTH_PAGE_PREFIXES);
  const signedIn = hasSession(req);

  // Signed-in users hitting /sign-in or /sign-up — bounce to their intended
  // destination (from ?return_to) or home. Prevents a logged-in user from
  // seeing a login form they don't need.
  if (isAuthPage && signedIn) {
    const rawReturn = req.nextUrl.searchParams.get("return_to");
    const safeReturn =
      rawReturn && rawReturn.startsWith("/") && !startsWithAny(rawReturn, AUTH_PAGE_PREFIXES)
        ? rawReturn
        : "/";
    const dest = req.nextUrl.clone();
    dest.pathname = safeReturn;
    dest.search = "";
    return NextResponse.redirect(dest);
  }

  // Unauth'd users on auth pages — let them through.
  if (isAuthPage) return NextResponse.next();

  // Anyone else without a session — redirect to sign-in with return_to.
  if (!signedIn) {
    const signIn = req.nextUrl.clone();
    signIn.pathname = "/sign-in";
    signIn.search = "";
    signIn.searchParams.set("return_to", pathname + (search || ""));
    return NextResponse.redirect(signIn);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
