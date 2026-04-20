import { redirect } from "next/navigation";
import { DevModeAuthCard } from "@/components/auth/dev-mode-card";

/**
 * Sign-up entry point. See SignInPage for the dev-mode vs stack-on rationale.
 */
export default function SignUpPage() {
  const configured = Boolean(
    process.env.NEXT_PUBLIC_STACK_PROJECT_ID &&
    process.env.NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY,
  );
  if (configured) {
    redirect("/handler/sign-up");
  }
  return <DevModeAuthCard mode="sign-up" />;
}
