"use client";

/**
 * OAuth completion handoff page.
 *
 * The backend mints a JWT after a successful GitHub OAuth exchange and
 * 302-redirects to `/auth/complete?token=<jwt>&return_to=<path>`. This page:
 *
 *   1. Reads the token + return_to from the URL.
 *   2. Fetches GET /api/v1/me with the token to hydrate the stored user.
 *   3. Calls setSession(token, user) — same store used by email/password auth.
 *   4. router.replace(returnTo) — navigates away, which strips the token from
 *      the browser's URL bar (and from history once another navigation runs).
 *
 * Errors redirect to /sign-in?error=<code> so the sign-in page can show them.
 */

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { fetchMeWithToken, setSession } from "@/lib/auth";

export default function AuthCompletePage() {
  return (
    <React.Suspense fallback={<CompleteShell label="Signing you in…" />}>
      <AuthCompleteInner />
    </React.Suspense>
  );
}

function AuthCompleteInner() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token");
  const rawReturn = params.get("return_to");
  const returnTo = rawReturn && rawReturn.startsWith("/") ? rawReturn : "/";

  React.useEffect(() => {
    if (!token) {
      router.replace("/sign-in?error=oauth_incomplete");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const user = await fetchMeWithToken(token);
        if (cancelled) return;
        setSession(token, user);
        router.replace(returnTo);
        router.refresh();
      } catch {
        if (cancelled) return;
        router.replace("/sign-in?error=oauth_me_failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, returnTo, router]);

  return <CompleteShell label="Signing you in…" />;
}

function CompleteShell({ label }: { label: string }) {
  return (
    <div className="min-h-screen grid place-items-center bg-background text-muted-foreground text-sm">
      {label}
    </div>
  );
}
