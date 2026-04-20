import { StackHandler } from "@stackframe/stack";
import { stackServerApp, stackAuthEnabled } from "@/stack";

/**
 * Catch-all handler for Stack Auth flows: sign-in, sign-up, email
 * verification, password reset, OAuth callback. Every path Stack's client
 * SDK redirects to lives under `/handler/...`.
 *
 * When Stack is not configured (Phase 1b with env vars still unset), this
 * page renders a friendly note so the UX doesn't crash.
 */

export default function Handler(props: unknown) {
  if (!stackAuthEnabled || !stackServerApp) {
    return (
      <main className="max-w-xl mx-auto py-16 px-6 space-y-3">
        <h1 className="text-xl font-semibold">Sign-in not configured</h1>
        <p className="text-sm text-muted-foreground">
          Stack Auth environment variables are not set on this deployment.
          Add NEXT_PUBLIC_STACK_PROJECT_ID, NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY
          and STACK_SECRET_SERVER_KEY, then redeploy.
        </p>
      </main>
    );
  }
  return <StackHandler fullPage app={stackServerApp} routeProps={props} />;
}
