import "server-only";
import { StackServerApp } from "@stackframe/stack";

/**
 * Neon Auth server-app singleton (Neon Auth is a Neon-hosted Stack Auth
 * tenant; the SDK is @stackframe/stack with a custom baseUrl).
 *
 * Initialised lazily so the app can still boot without credentials. When
 * any required env var is missing, ``stackServerApp`` is ``null`` and the
 * /handler route renders a "not configured" card.
 *
 * Required env vars (set all four or none):
 *   - NEON_AUTH_BASE_URL
 *   - NEXT_PUBLIC_NEON_AUTH_PROJECT_ID
 *   - NEXT_PUBLIC_NEON_AUTH_PUBLISHABLE_CLIENT_KEY
 *   - NEON_AUTH_SECRET_SERVER_KEY
 */

const baseUrl = process.env.NEON_AUTH_BASE_URL;
const projectId = process.env.NEXT_PUBLIC_NEON_AUTH_PROJECT_ID;
const publishableClientKey = process.env.NEXT_PUBLIC_NEON_AUTH_PUBLISHABLE_CLIENT_KEY;
const secretServerKey = process.env.NEON_AUTH_SECRET_SERVER_KEY;

export const neonAuthEnabled = Boolean(
  baseUrl && projectId && publishableClientKey && secretServerKey,
);

export const stackServerApp = neonAuthEnabled
  ? new StackServerApp({
      tokenStore: "nextjs-cookie",
      baseUrl: baseUrl!,
      projectId: projectId!,
      publishableClientKey: publishableClientKey!,
      secretServerKey: secretServerKey!,
    })
  : null;
