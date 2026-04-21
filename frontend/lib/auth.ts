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
import { resetLocalUserState } from "./session-reset";

export type AuthUser = {
  id: string;
  email: string;
  display_name: string | null;
};

export type SignupResult = {
  user: AuthUser;
  /** true when Supabase returned a session — user is logged in and can be
   *  redirected straight to the app. false when email confirmation is
   *  required — the caller should show a "check your inbox" message. */
  signedIn: boolean;
};

function toAuthUser(user: import("@supabase/supabase-js").User): AuthUser {
  const meta = user.user_metadata ?? {};
  return {
    id: user.id,
    email: user.email ?? "",
    display_name: (meta.display_name as string | null) ?? (meta.full_name as string | null) ?? null,
  };
}

/** Sign up with email + password. Pass display_name in user_metadata.
 *
 *  Returns `signedIn: false` when Supabase has email confirmation enabled —
 *  the account exists but the user must verify their inbox before the next
 *  `signInWithPassword`. Without this flag the sign-up page would redirect
 *  to `/`, middleware would see no session, and bounce back to `/sign-in`
 *  with no explanation. */
export async function signup(input: {
  email: string;
  password: string;
  display_name?: string;
}): Promise<SignupResult> {
  const supabase = getSupabaseBrowserClient();
  const { data, error } = await supabase.auth.signUp({
    email: input.email,
    password: input.password,
    options: {
      data: input.display_name ? { display_name: input.display_name } : undefined,
      emailRedirectTo:
        typeof window !== "undefined"
          ? `${window.location.origin}/auth/callback`
          : undefined,
    },
  });
  if (error) throw new Error(error.message);
  if (!data.user) throw new Error("Sign-up succeeded but no user returned.");
  return { user: toAuthUser(data.user), signedIn: data.session !== null };
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

/** Sign out — clears Supabase session (localStorage + cookies) AND wipes
 *  any workspace / chat / wizard state so the next user on this device
 *  starts from a clean slate. The AuthGate listener also reacts to
 *  SIGNED_OUT and purges the tanstack-query cache. */
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
  resetLocalUserState();
  await supabase.auth.signOut();
}
