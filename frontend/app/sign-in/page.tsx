import { redirect } from "next/navigation";
import { DevModeAuthCard } from "@/components/auth/dev-mode-card";

/**
 * Sign-in entry point.
 * - When Stack Auth is configured: fast redirect to Stack's handler route.
 * - When not configured: a clear dev-mode page explaining why there's no
 *   login form and what env vars turn it on. Avoids the dead-end UX where
 *   a user clicks "Sign in" and gets no visible response.
 */
export default function SignInPage() {
  const configured = Boolean(
    process.env.NEXT_PUBLIC_STACK_PROJECT_ID &&
    process.env.NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY,
  );
  if (configured) {
    redirect("/handler/sign-in");
  }
  return <DevModeAuthCard mode="sign-in" />;
}
