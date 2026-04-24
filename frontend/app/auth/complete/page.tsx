"use client";

/**
 * OAuth completion handoff page.
 *
 * The backend mints a JWT after a successful GitHub OAuth exchange and
 * 302-redirects to `/auth/complete?return_to=<path>#token=<jwt>`. This page:
 *
 *   1. Reads the token from `window.location.hash` (fragments never hit the
 *      network, so the JWT is not in Render's access logs or the Referer
 *      header). `return_to` stays in the query string.
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

function readHashToken(): string | null {
  if (typeof window === "undefined") return null;
  const raw = window.location.hash;
  if (!raw || raw.length < 2) return null;
  const params = new URLSearchParams(raw.slice(1));
  return params.get("token");
}

function AuthCompleteInner() {
  const router = useRouter();
  const params = useSearchParams();
  const rawReturn = params.get("return_to");
  const returnTo = rawReturn && rawReturn.startsWith("/") ? rawReturn : "/";

  React.useEffect(() => {
    const token = readHashToken();
    if (!token) {
      router.replace("/sign-in?error=oauth_incomplete");
      return;
    }
    // Scrub the fragment immediately so a follow-up refresh doesn't re-read
    // a stale token from the URL bar.
    try {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    } catch {
      // Older browsers / restrictive sandboxes — not worth failing for.
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
  }, [returnTo, router]);

  return <CompleteShell label="Signing you in…" />;
}

function CompleteShell({ label }: { label: string }) {
  return (
    <div className="min-h-screen grid place-items-center bg-background text-muted-foreground text-sm">
      {label}
    </div>
  );
}
