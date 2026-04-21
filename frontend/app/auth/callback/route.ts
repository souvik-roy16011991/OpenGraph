import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

/**
 * OAuth callback handler.
 *
 * After a Google / GitHub OAuth flow Supabase redirects the user here with a
 * `code` query param. We exchange it for a session (access + refresh tokens)
 * which Supabase writes into cookies, then redirect to the original destination.
 *
 * Supabase can also redirect here with `?error=...&error_description=...` when
 * the user cancels consent or the provider rejects the request — surface that
 * on the sign-in page instead of silently landing them at `/`.
 */
export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const err = request.nextUrl.searchParams.get("error");
  const errDesc = request.nextUrl.searchParams.get("error_description");
  const rawReturn = request.nextUrl.searchParams.get("return_to") ?? "/";
  const returnTo = rawReturn.startsWith("/") ? rawReturn : "/";

  const bounceToSignIn = (message: string) => {
    const url = new URL("/sign-in", request.url);
    url.searchParams.set("error", message);
    if (rawReturn && rawReturn.startsWith("/")) {
      url.searchParams.set("return_to", rawReturn);
    }
    return NextResponse.redirect(url);
  };

  if (err) {
    return bounceToSignIn(errDesc || err);
  }

  if (!code) {
    return bounceToSignIn("Missing OAuth code. Please try signing in again.");
  }

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

  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) {
    return bounceToSignIn(
      error.message || "Sign-in failed. Please try again.",
    );
  }

  return NextResponse.redirect(new URL(returnTo, request.url));
}
