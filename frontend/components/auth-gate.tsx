"use client";

import * as React from "react";
import { usePathname, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { getSupabaseBrowserClient } from "@/lib/supabase";
import { resetLocalUserState } from "@/lib/session-reset";
import { setCachedAccessToken } from "@/lib/auth-token";

// Keep in sync with middleware's auth-page list. AuthGate must never bounce
// the user OFF these pages when it sees SIGNED_OUT — they ARE the sign-out
// destination. Without this check, signing out while on /sign-in triggers
// router.replace("/sign-in") which is a no-op but still fires router.refresh()
// loops in some browsers.
const AUTH_PATH_PREFIXES = ["/sign-in", "/sign-up", "/auth/"];

function isAuthPath(pathname: string | null): boolean {
  if (!pathname) return false;
  return AUTH_PATH_PREFIXES.some((p) => pathname.startsWith(p));
}

/**
 * Global auth lifecycle handler.
 *
 * Wraps the app tree and listens for Supabase auth changes:
 *   - On the very first load, records the current user id in a ref.
 *   - SIGNED_OUT  → wipe per-user local state + query cache, bounce to /sign-in.
 *   - Any event whose session.user.id differs from the last-seen id → the
 *     user on this device just changed. Wipe state before the app can
 *     render with the previous user's selections still visible.
 *
 * This is the single chokepoint. Components never need to manage
 * cross-user cleanup themselves.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const lastUserIdRef = React.useRef<string | null>(null);
  const initialisedRef = React.useRef(false);
  const [ready, setReady] = React.useState(false);

  // Keep latest pathname in a ref so the SIGNED_OUT handler can read it
  // without re-subscribing on every navigation (subscribing once per mount
  // keeps Supabase from firing INITIAL_SESSION repeatedly).
  const pathnameRef = React.useRef(pathname);
  React.useEffect(() => {
    pathnameRef.current = pathname;
  }, [pathname]);

  React.useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    let cancelled = false;

    // Seed the ref + access-token cache from the current session so the
    // first `onAuthStateChange` event (which fires with INITIAL_SESSION)
    // doesn't register a false "user switch" against a null baseline.
    // Gate rendering until this resolves so React Query's first fetch
    // always sees a populated token cache.
    //
    // Always unblock rendering even if getSession fails (bad SUPABASE_URL,
    // network down, etc.) — without this, a transient error at boot
    // produces a permanent blank screen.
    supabase.auth
      .getSession()
      .then(({ data }) => {
        if (cancelled) return;
        lastUserIdRef.current = data.session?.user.id ?? null;
        setCachedAccessToken(data.session?.access_token ?? null);
      })
      .catch((err) => {
        // eslint-disable-next-line no-console
        console.warn("AuthGate: initial getSession failed", err);
      })
      .finally(() => {
        if (cancelled) return;
        initialisedRef.current = true;
        setReady(true);
      });

    const { data: sub } = supabase.auth.onAuthStateChange((event, session) => {
      const newId = session?.user.id ?? null;
      const prevId = lastUserIdRef.current;

      // Keep the in-memory bearer token in sync with every refresh.
      setCachedAccessToken(session?.access_token ?? null);

      if (event === "SIGNED_OUT") {
        lastUserIdRef.current = null;
        resetLocalUserState(queryClient);
        // Don't bounce if we're already on an auth page — otherwise the
        // sign-in page itself would trigger a redirect loop when a user
        // lands there unauthenticated.
        if (!isAuthPath(pathnameRef.current)) {
          router.replace("/sign-in");
          router.refresh();
        }
        return;
      }

      // Ignore the seed event — we already captured it above.
      if (!initialisedRef.current) {
        lastUserIdRef.current = newId;
        return;
      }

      if (newId && prevId && newId !== prevId) {
        // A different user just signed in on this device (e.g. OAuth
        // callback completed for a new account without an explicit logout).
        resetLocalUserState(queryClient);
      }
      lastUserIdRef.current = newId;
    });

    return () => {
      cancelled = true;
      sub.subscription.unsubscribe();
    };
  }, [queryClient, router]);

  if (!ready) return null;
  return <>{children}</>;
}
