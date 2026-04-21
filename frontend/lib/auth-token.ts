"use client";

/**
 * In-memory access-token cache.
 *
 * `@supabase/ssr`'s browser client stores the session in cookies (so the
 * Next.js middleware can read it) — NOT localStorage. That leaves our
 * synchronous `fetch` helpers with nowhere to pull the bearer token from
 * at call time.
 *
 * This module keeps the current access token in a module-level variable,
 * primed once per tab via `supabase.auth.getSession()` and refreshed by
 * `onAuthStateChange` on every token rotation / sign-in / sign-out.
 *
 * Both the subscription in `AuthGate` and the lazy prime in
 * `primeTokenCache()` are idempotent — whichever runs first wins; the
 * other is a no-op.
 */

import { getSupabaseBrowserClient } from "./supabase";

let cachedToken: string | null = null;
let primed = false;

export function primeTokenCache(): void {
  if (primed || typeof window === "undefined") return;
  primed = true;
  const supabase = getSupabaseBrowserClient();
  supabase.auth
    .getSession()
    .then(({ data }) => {
      // Only overwrite if AuthGate hasn't already set something fresher.
      if (cachedToken === null) {
        cachedToken = data.session?.access_token ?? null;
      }
    })
    .catch(() => {});
  supabase.auth.onAuthStateChange((_event, session) => {
    cachedToken = session?.access_token ?? null;
  });
}

export function getCachedAccessToken(): string | null {
  return cachedToken;
}

export function setCachedAccessToken(token: string | null): void {
  cachedToken = token;
}
