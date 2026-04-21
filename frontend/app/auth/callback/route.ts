import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

/**
 * OAuth callback handler.
 *
 * After a Google / GitHub OAuth flow Supabase redirects the user here with a
 * `code` query param. We exchange it for a session (access + refresh tokens)
 * which Supabase writes into cookies, then redirect to the original destination.
 */
export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const rawReturn = request.nextUrl.searchParams.get("return_to") ?? "/";
  const returnTo = rawReturn.startsWith("/") ? rawReturn : "/";

  if (code) {
    const cookieStore = await cookies();
    const supabase = createServerClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
      {
        cookies: {
          getAll() {
            return cookieStore.getAll();
          },
          setAll(cookiesToSet) {
            cookiesToSet.forEach(({ name, value, options }) =>
              cookieStore.set(name, value, options),
            );
          },
        },
      },
    );
    await supabase.auth.exchangeCodeForSession(code);
  }

  return NextResponse.redirect(new URL(returnTo, request.url));
}
