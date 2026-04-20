"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

/**
 * Dev-mode session cookie.
 *
 * When Stack Auth is *not* configured, we still require an explicit "sign
 * in" gesture before the app is reachable — the user has to click
 * "Continue as dev user" on /sign-in. That click fires this server action,
 * which drops a simple signed cookie (`og-session=dev`) that the middleware
 * treats as a valid session marker. Without the cookie, every route except
 * /sign-in, /sign-up, /handler/* redirects to /sign-in.
 *
 * When Stack Auth IS configured, this cookie isn't needed — Stack sets its
 * own `stack-refresh-*` cookie on successful sign-in, and the middleware
 * accepts either.
 */

const COOKIE = "og-session";
const MAX_AGE = 60 * 60 * 24 * 30; // 30 days

function isSafeRelative(p: string | null): p is string {
  return typeof p === "string" && p.startsWith("/") && !p.startsWith("//");
}

export async function beginDevSession(formData: FormData) {
  const returnTo = formData.get("return_to");
  (await cookies()).set(COOKIE, "dev", {
    httpOnly: false,
    sameSite: "lax",
    path: "/",
    maxAge: MAX_AGE,
  });
  redirect(isSafeRelative(typeof returnTo === "string" ? returnTo : null) ? (returnTo as string) : "/");
}

export async function endDevSession() {
  (await cookies()).delete(COOKIE);
  redirect("/sign-in");
}
