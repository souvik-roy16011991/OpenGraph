import { redirect } from "next/navigation";

/**
 * Sign-in entry point — forwards to Neon Auth's /handler flow, preserving
 * the caller's intended destination via `after_auth_return_to`. If Neon
 * Auth env vars are missing the /handler route renders a "not configured"
 * card instead.
 */
export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const rawReturn = params?.return_to;
  const returnTo = typeof rawReturn === "string" ? rawReturn : undefined;
  const target = returnTo
    ? `/handler/sign-in?after_auth_return_to=${encodeURIComponent(returnTo)}`
    : "/handler/sign-in";
  redirect(target);
}
