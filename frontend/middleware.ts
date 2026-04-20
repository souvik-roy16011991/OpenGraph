import { NextRequest, NextResponse } from "next/server";

/**
 * Auth gate.
 *
 * When Stack Auth env vars are configured (production), require an auth
 * cookie on every page except Stack's own /handler/* routes, public assets,
 * and API routes (the backend enforces its own auth). Unauthenticated
 * visitors bounce to Stack's sign-in handler at /handler/sign-in.
 *
 * When Stack Auth is NOT configured (dev / Phase 1b still in effect), this
 * middleware is a no-op so local development keeps working without
 * credentials. Flipping on enforcement is literally "add env vars +
 * redeploy" — no code change required.
 *
 * Matcher below excludes static assets and Next internals; the runtime
 * checks inside this function do the rest.
 */

const PUBLIC_PREFIXES = [
  "/handler",       // Stack Auth flows (sign-in, callback, reset)
  "/sign-in",       // Convenience shortcut → redirects to /handler/sign-in
  "/sign-up",       // Convenience shortcut → redirects to /handler/sign-up
  "/_next",         // Next internals
  "/favicon.ico",
];

function isPublic(pathname: string): boolean {
  return PUBLIC_PREFIXES.some((p) => pathname.startsWith(p));
}

export function middleware(req: NextRequest) {
  const projectId = process.env.NEXT_PUBLIC_STACK_PROJECT_ID;
  const publishable = process.env.NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY;
  // Soft-rollout: no gate until Stack is actually configured.
  if (!projectId || !publishable) {
    return NextResponse.next();
  }

  const { pathname } = req.nextUrl;
  if (isPublic(pathname)) {
    return NextResponse.next();
  }

  // Stack Auth cookies include `stack-access` and `stack-refresh`. Presence
  // of stack-refresh is a good-enough proxy for "might be signed in" — the
  // backend still validates the JWT on every API call, so a forged cookie
  // grants nothing beyond the ability to see the UI shell.
  const hasSession = req.cookies
    .getAll()
    .some((c) => c.name.startsWith("stack-refresh"));
  if (hasSession) {
    return NextResponse.next();
  }

  const signIn = req.nextUrl.clone();
  signIn.pathname = "/handler/sign-in";
  signIn.searchParams.set("after_auth_return_to", pathname + req.nextUrl.search);
  return NextResponse.redirect(signIn);
}

export const config = {
  // Exclude static files so the middleware doesn't fire on every .js / .css request.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
