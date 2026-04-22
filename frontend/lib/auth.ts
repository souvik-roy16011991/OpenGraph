"use client";

/**
 * Client-side JWT session helpers.
 *
 * The backend issues an HS256 JWT via POST /api/v1/auth/{signup,login}. We
 * persist it in two places:
 *
 *   - ``localStorage["auth_token"]`` — read by ``lib/api.ts`` to put the
 *     token in ``Authorization: Bearer <token>`` on every fetch.
 *   - A non-HttpOnly cookie ``auth_token`` — read by Next.js middleware so
 *     a request without a session is redirected to /sign-in before any
 *     page JS runs.
 *
 * Both stores are kept in sync by ``setSession`` / ``clearSession``. Middleware
 * only needs presence; the actual signature check happens on the backend.
 */

export type StoredUser = {
  id: string;
  email: string;
  display_name?: string | null;
};

const TOKEN_KEY = "auth_token";
const USER_KEY = "auth_user";
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30; // 30 days — match JWT_EXPIRES_MINUTES default

function setCookie(name: string, value: string, maxAgeSeconds: number): void {
  if (typeof document === "undefined") return;
  const secure = typeof window !== "undefined" && window.location.protocol === "https:";
  const attrs = [
    `${name}=${encodeURIComponent(value)}`,
    "Path=/",
    `Max-Age=${maxAgeSeconds}`,
    "SameSite=Lax",
    secure ? "Secure" : "",
  ].filter(Boolean);
  document.cookie = attrs.join("; ");
}

function clearCookie(name: string): void {
  if (typeof document === "undefined") return;
  document.cookie = `${name}=; Path=/; Max-Age=0; SameSite=Lax`;
}

export function setSession(token: string, user: StoredUser): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
  setCookie(TOKEN_KEY, token, COOKIE_MAX_AGE_SECONDS);
}

export function clearSession(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
  // Also drop the persisted zustand workspace selection. Leaving it behind
  // means the next user to sign in on this browser inherits the previous
  // user's active workspace id, which the backend then 404s as "Workspace X
  // not found" (same response as cross-tenant access — see src/api/deps.py).
  window.localStorage.removeItem("kb-active-workspace");
  clearCookie(TOKEN_KEY);
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser(): StoredUser | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as StoredUser;
  } catch {
    return null;
  }
}

export type AuthResponse = { token: string; token_type: string; user: StoredUser };

// Render's `fromService.property: host` yields a bare hostname — prepend
// https:// when the scheme is missing so the fetch URL is absolute (otherwise
// the browser resolves it as a relative path and returns the host's HTML).
const RAW_API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "").replace(/\/$/, "");
const API_BASE = RAW_API_BASE && !/^https?:\/\//i.test(RAW_API_BASE)
  ? `https://${RAW_API_BASE}`
  : RAW_API_BASE;

async function authFetch(path: string, body: unknown): Promise<AuthResponse> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch (err) {
    if (err instanceof TypeError) {
      throw new Error("Can't reach the server right now — please check your internet connection and try again.");
    }
    throw err;
  }
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<AuthResponse>;
}

export async function signup(input: {
  email: string;
  password: string;
  display_name?: string;
}): Promise<AuthResponse> {
  const out = await authFetch("/api/v1/auth/signup", input);
  setSession(out.token, out.user);
  return out;
}

export async function login(input: { email: string; password: string }): Promise<AuthResponse> {
  const out = await authFetch("/api/v1/auth/login", input);
  setSession(out.token, out.user);
  return out;
}

/**
 * Build the URL that kicks off the GitHub OAuth flow. Returning a string (not
 * navigating here) keeps this SSR-safe and lets the caller choose between a
 * full-page `window.location.href = ...` and a Link/Button.
 *
 * The browser navigates to this backend URL; the backend 302-redirects to
 * GitHub, GitHub calls back to the backend with the OAuth code, the backend
 * mints a JWT and 302-redirects to `/auth/complete?token=...` — the callback
 * page (`app/auth/complete/page.tsx`) stores the token and navigates to
 * `returnTo` (or `/`).
 */
export function githubLoginUrl(returnTo?: string | null): string {
  const qs = returnTo ? `?return_to=${encodeURIComponent(returnTo)}` : "";
  return `${API_BASE}/api/v1/auth/github/login${qs}`;
}

/**
 * Fetch /me with an explicit token — used by the OAuth callback page where
 * ``setSession`` hasn't run yet, so ``lib/api.ts`` can't read the token from
 * ``localStorage``. On success returns the same shape as ``signup``/``login``.
 */
export async function fetchMeWithToken(token: string): Promise<StoredUser> {
  const res = await fetch(`${API_BASE}/api/v1/me`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const data = await res.json();
  return {
    id: String(data.id),
    email: String(data.email ?? ""),
    display_name: data.display_name ?? null,
  };
}

export function logout(): void {
  // Fire-and-forget the server audit; clear local session either way.
  const token = getToken();
  if (token) {
    fetch(`${API_BASE}/api/v1/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
      keepalive: true,
    }).catch(() => { /* ignore network errors on logout */ });
  }
  clearSession();
}
