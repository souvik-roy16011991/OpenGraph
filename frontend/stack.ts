import "server-only";
import { StackServerApp } from "@stackframe/stack";

/**
 * Stack Auth server-app singleton.
 *
 * Initialised lazily so the app can still boot without Stack Auth credentials
 * (Phase 1b soft-rollout: auth is parsed when present, never required). When
 * any of the three env vars is missing, ``stackServerApp`` is ``null`` and
 * the layout falls through to the unauthenticated path.
 *
 * Required env vars (set all three or none):
 *   - NEXT_PUBLIC_STACK_PROJECT_ID
 *   - NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY
 *   - STACK_SECRET_SERVER_KEY
 */

const projectId = process.env.NEXT_PUBLIC_STACK_PROJECT_ID;
const publishableClientKey = process.env.NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY;
const secretServerKey = process.env.STACK_SECRET_SERVER_KEY;

export const stackAuthEnabled = Boolean(
  projectId && publishableClientKey && secretServerKey,
);

export const stackServerApp = stackAuthEnabled
  ? new StackServerApp({
      tokenStore: "nextjs-cookie",
      projectId: projectId!,
      publishableClientKey: publishableClientKey!,
      secretServerKey: secretServerKey!,
    })
  : null;
