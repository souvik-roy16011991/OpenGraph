import { createServerClient } from "@supabase/ssr";
import { NextRequest, NextResponse } from "next/server";

const PUBLIC_PREFIXES = [
  "/sign-in",
  "/sign-up",
  "/auth/callback",
  "/_next",
  "/favicon",
  "/opengraph-mark.svg",
];

function isPublic(pathname: string): boolean {
  return PUBLIC_PREFIXES.some((p) => pathname.startsWith(p));
}

export async function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  if (isPublic(pathname)) return NextResponse.next();

  // Build a response we can write Supabase session cookies into.
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

  // getSession() also silently refreshes an expired access token and writes
  // the new tokens into `res` via the setAll cookie handler above.
  const {
    data: { session },
  } = await supabase.auth.getSession();

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
