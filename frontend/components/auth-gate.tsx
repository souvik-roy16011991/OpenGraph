"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { getSupabaseBrowserClient } from "@/lib/supabase";
import { resetLocalUserState } from "@/lib/session-reset";

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
  const queryClient = useQueryClient();
  const lastUserIdRef = React.useRef<string | null>(null);
  const initialisedRef = React.useRef(false);

  React.useEffect(() => {
    const supabase = getSupabaseBrowserClient();

    // Seed the ref from the current session so the first `onAuthStateChange`
    // event (which fires with INITIAL_SESSION) doesn't register a false
    // "user switch" against a null baseline.
    supabase.auth.getSession().then(({ data }) => {
      lastUserIdRef.current = data.session?.user.id ?? null;
      initialisedRef.current = true;
    });

    const { data: sub } = supabase.auth.onAuthStateChange((event, session) => {
      const newId = session?.user.id ?? null;
      const prevId = lastUserIdRef.current;

      if (event === "SIGNED_OUT") {
        lastUserIdRef.current = null;
        resetLocalUserState(queryClient);
        router.replace("/sign-in");
        router.refresh();
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
      sub.subscription.unsubscribe();
    };
  }, [queryClient, router]);

  return <>{children}</>;
}
