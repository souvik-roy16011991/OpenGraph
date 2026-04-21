import { createServerClient } from "@supabase/ssr";
import { NextRequest, NextResponse } from "next/server";

/**
 * Three categories of request:
 *   1. Static / infra prefixes — always let through.
 *   2. Auth pages (/sign-in, /sign-up) — signed-in users get bounced to "/"
 *      (or ?return_to); anonymous users see the page.
 *   3. Everything else — requires a Supabase session; otherwise redirect to
 *      /sign-in?return_to=<path>.
 *
 * Supabase's SSR client both verifies and silently refreshes the access
 * token. Refreshed cookies are written back onto the response.
 */

const INFRA_PREFIXES = ["/_next", "/favicon", "/opengraph-mark.svg"];
const AUTH_PAGE_PREFIXES = ["/sign-in", "/sign-up"];
const AUTH_CALLBACK = "/auth/callback";

function startsWithAny(pathname: string, prefixes: string[]): boolean {
  return prefixes.some((p) => pathname.startsWith(p));
}

export async function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;

  if (startsWithAny(pathname, INFRA_PREFIXES)) return NextResponse.next();
  // OAuth code exchange must run without session gating.
  if (pathname.startsWith(AUTH_CALLBACK)) return NextResponse.next();

  const res = NextResponse.next();
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return req.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value, options }) =>
            res.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  const {
    data: { session },
  } = await supabase.auth.getSession();

  const isAuthPage = startsWithAny(pathname, AUTH_PAGE_PREFIXES);

  if (isAuthPage) {
    if (session) {
      const rawReturn = req.nextUrl.searchParams.get("return_to");
      const safeReturn = rawReturn && rawReturn.startsWith("/") && !startsWithAny(rawReturn, AUTH_PAGE_PREFIXES)
        ? rawReturn
        : "/";
      const dest = req.nextUrl.clone();
      dest.pathname = safeReturn;
      dest.search = "";
      return NextResponse.redirect(dest);
    }
    return res;
  }

  if (session) return res;

  const signIn = req.nextUrl.clone();
  signIn.pathname = "/sign-in";
  signIn.search = "";
  signIn.searchParams.set("return_to", pathname + (search || ""));
  return NextResponse.redirect(signIn);
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
