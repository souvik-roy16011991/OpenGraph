import { redirect } from "next/navigation";
import { DevModeAuthCard } from "@/components/auth/dev-mode-card";

/**
 * Sign-in entry point.
 *  - Stack Auth ON  → fast redirect to Stack's handler, preserving
 *    return_to via Stack's own `after_auth_return_to` param.
 *  - Stack Auth OFF → dev-mode info card whose "Continue as dev user"
 *    server action drops the `og-session=dev` cookie.
 */
export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const rawReturn = params?.return_to;
  const returnTo = typeof rawReturn === "string" ? rawReturn : undefined;

  const configured = Boolean(
    process.env.NEXT_PUBLIC_STACK_PROJECT_ID &&
      process.env.NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY,
  );
  if (configured) {
    const target = returnTo
      ? `/handler/sign-in?after_auth_return_to=${encodeURIComponent(returnTo)}`
      : "/handler/sign-in";
    redirect(target);
  }
  return <DevModeAuthCard mode="sign-in" returnTo={returnTo} />;
}
