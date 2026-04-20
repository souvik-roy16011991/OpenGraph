import { StackHandler } from "@stackframe/stack";
import { stackServerApp, neonAuthEnabled } from "@/stack";

/**
 * Catch-all handler for Neon Auth flows: sign-in, sign-up, email
 * verification, password reset, OAuth callback. Every path the SDK
 * redirects to lives under `/handler/...`.
 *
 * When Neon Auth is not configured, render a friendly note listing the
 * required env vars instead of crashing.
 */

export default function Handler(props: unknown) {
  if (!neonAuthEnabled || !stackServerApp) {
    return (
      <main className="max-w-xl mx-auto py-16 px-6 space-y-3">
        <h1 className="text-xl font-semibold">Sign-in not configured</h1>
        <p className="text-sm text-muted-foreground">
          Neon Auth environment variables are not set on this deployment.
          Add NEON_AUTH_BASE_URL, NEXT_PUBLIC_NEON_AUTH_PROJECT_ID,
          NEXT_PUBLIC_NEON_AUTH_PUBLISHABLE_CLIENT_KEY, and
          NEON_AUTH_SECRET_SERVER_KEY, then redeploy.
        </p>
      </main>
    );
  }
  return <StackHandler fullPage app={stackServerApp} routeProps={props} />;
}
