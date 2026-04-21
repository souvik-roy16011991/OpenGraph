"use client";

/**
 * Auth helpers — thin wrappers around the Supabase browser client.
 *
 * Supabase manages token storage (localStorage + cookies via @supabase/ssr),
 * auto-refresh, and session persistence. This module exposes a stable API
 * used by sign-in/sign-up pages and the logout flow.
 */

import type { Provider } from "@supabase/supabase-js";
import { getSupabaseBrowserClient } from "./supabase";

export type AuthUser = {
  id: string;
  email: string;
  display_name: string | null;
};

function toAuthUser(user: import("@supabase/supabase-js").User): AuthUser {
  const meta = user.user_metadata ?? {};
  return {
    id: user.id,
    email: user.email ?? "",
    display_name: (meta.display_name as string | null) ?? (meta.full_name as string | null) ?? null,
  };
}

/** Sign up with email + password. Pass display_name in user_metadata. */
export async function signup(input: {
  email: string;
  password: string;
  display_name?: string;
}): Promise<AuthUser> {
  const supabase = getSupabaseBrowserClient();
  const { data, error } = await supabase.auth.signUp({
    email: input.email,
    password: input.password,
    options: {
      data: input.display_name ? { display_name: input.display_name } : undefined,
    },
  });
  if (error) throw new Error(error.message);
  if (!data.user) throw new Error("Sign-up succeeded but no user returned.");
  return toAuthUser(data.user);
}

/** Sign in with email + password. */
export async function login(input: { email: string; password: string }): Promise<AuthUser> {
  const supabase = getSupabaseBrowserClient();
  const { data, error } = await supabase.auth.signInWithPassword({
    email: input.email,
    password: input.password,
  });
  if (error) throw new Error(error.message);
  if (!data.user) throw new Error("Login succeeded but no user returned.");
  return toAuthUser(data.user);
}

/** Sign in via OAuth provider (Google or GitHub). Redirects to Supabase and
 *  then back to /auth/callback which exchanges the code for a session. */
export async function loginWithOAuth(provider: "google" | "github"): Promise<void> {
  const supabase = getSupabaseBrowserClient();
  const redirectTo = `${window.location.origin}/auth/callback`;
  const { error } = await supabase.auth.signInWithOAuth({
    provider: provider as Provider,
    options: { redirectTo },
  });
  if (error) throw new Error(error.message);
}

/** Sign out — clears Supabase session (localStorage + cookies). Optionally
 *  calls the backend audit endpoint fire-and-forget. */
export async function logout(): Promise<void> {
  const supabase = getSupabaseBrowserClient();
  // Fire-and-forget backend audit event; ignore errors (token may be stale).
  supabase.auth.getSession().then(({ data }) => {
    const token = data.session?.access_token;
    if (token) {
      const base = (process.env.NEXT_PUBLIC_API_BASE ?? "").replace(/\/$/, "");
      fetch(`${base}/api/v1/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        keepalive: true,
      }).catch(() => {});
    }
  });
  await supabase.auth.signOut();
}

/** Return the current session's access token synchronously from localStorage,
 *  or null if not signed in. Used by lib/api.ts which is sync. */
export function getTokenSync(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
    const ref = url.replace("https://", "").split(".")[0];
    const raw = window.localStorage.getItem(`sb-${ref}-auth-token`);
    if (!raw) return null;
    return (JSON.parse(raw) as { access_token?: string }).access_token ?? null;
  } catch {
    return null;
  }
}
