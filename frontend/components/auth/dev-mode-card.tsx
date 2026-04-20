"use client";

import Link from "next/link";
import { ArrowRight, Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

/**
 * Shown on /sign-in and /sign-up when Stack Auth isn't configured.
 *
 * Explicit about the state (dev mode), explicit about how to turn real auth
 * on (three env vars, with links to where to get them), explicit about the
 * escape hatch (continue anyway as the shared dev user).
 */
export function DevModeAuthCard({ mode }: { mode: "sign-in" | "sign-up" }) {
  const title = mode === "sign-up" ? "Sign up" : "Sign in";
  return (
    <main className="mx-auto max-w-xl py-12 px-6 space-y-6">
      <div className="flex items-start gap-3">
        <div className="h-10 w-10 rounded-full bg-amber-500/15 flex items-center justify-center shrink-0">
          <Info className="h-5 w-5 text-amber-600 dark:text-amber-400" />
        </div>
        <div>
          <h1 className="text-xl font-semibold">{title} — dev mode</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Real authentication is handled by{" "}
            <a
              href="https://neon.tech/docs/guides/neon-auth"
              className="underline hover:text-foreground"
              target="_blank"
              rel="noreferrer"
            >
              Neon Auth (Stack Auth)
            </a>
            . It's currently <strong>off</strong> because the environment
            variables below aren't set, so the backend runs every request as
            a shared anonymous user.
          </p>
        </div>
      </div>

      <Card>
        <CardContent className="p-5 space-y-4">
          <h2 className="text-sm font-medium">Turn on real sign-in</h2>
          <ol className="text-sm text-muted-foreground space-y-3 list-decimal pl-5">
            <li>
              Create a project at{" "}
              <a
                href="https://app.stack-auth.com"
                className="underline hover:text-foreground font-mono text-xs"
                target="_blank"
                rel="noreferrer"
              >
                app.stack-auth.com
              </a>{" "}
              (or enable Auth in your Neon project dashboard — Neon provisions
              it under the hood).
            </li>
            <li>
              Copy the three keys. On the <strong>frontend</strong>, set:
              <pre className="mt-1.5 rounded bg-muted/50 px-3 py-2 text-[11px] font-mono overflow-x-auto">
NEXT_PUBLIC_STACK_PROJECT_ID={"{project id}"}
NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY={"{client key}"}
STACK_SECRET_SERVER_KEY={"{server secret}"}
              </pre>
            </li>
            <li>
              On the <strong>backend</strong>, set{" "}
              <code className="font-mono text-[11px]">STACK_PROJECT_ID</code>{" "}
              and{" "}
              <code className="font-mono text-[11px]">STACK_SECRET_SERVER_KEY</code>{" "}
              (same values).
            </li>
            <li>Redeploy both services; real sign-in takes over.</li>
          </ol>
          <p className="text-[11px] text-muted-foreground">
            See <a href="/DEPLOY.md" className="underline">DEPLOY.md §5</a> for
            the production cutover checklist (includes wiping anonymous data).
          </p>
        </CardContent>
      </Card>

      <div className="flex items-center gap-3 flex-wrap">
        <Button asChild variant="default">
          <Link href="/templates">
            Continue as dev user <ArrowRight className="h-4 w-4" />
          </Link>
        </Button>
        {mode === "sign-in" ? (
          <Button asChild variant="ghost">
            <Link href="/sign-up">New here? Sign up instead</Link>
          </Button>
        ) : (
          <Button asChild variant="ghost">
            <Link href="/sign-in">Already have an account? Sign in</Link>
          </Button>
        )}
      </div>
    </main>
  );
}
