import { redirect } from "next/navigation";
import { DevModeAuthCard } from "@/components/auth/dev-mode-card";

/** Sign-up entry point. See SignInPage for the dev-vs-stack rationale. */
export default async function SignUpPage({
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
      ? `/handler/sign-up?after_auth_return_to=${encodeURIComponent(returnTo)}`
      : "/handler/sign-up";
    redirect(target);
  }
  return <DevModeAuthCard mode="sign-up" returnTo={returnTo} />;
}
