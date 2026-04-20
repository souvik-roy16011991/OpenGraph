"use client";

import * as React from "react";
import Link from "next/link";
import { LogOut, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Header user menu.
 *
 * When Stack Auth is configured (both NEXT_PUBLIC_* env vars set), shows the
 * signed-in user's email + a sign-out action. When Stack Auth is not
 * configured, shows a muted "Dev mode" label — the backend is running
 * against the shared dev anon user.
 *
 * The Stack SDK's ``useUser()`` hook would throw if no StackProvider is
 * mounted above, so we guard via the same env check that the layout uses
 * to decide whether to mount StackProvider.
 */

const stackConfigured =
  typeof process.env.NEXT_PUBLIC_STACK_PROJECT_ID === "string" &&
  process.env.NEXT_PUBLIC_STACK_PROJECT_ID.length > 0 &&
  typeof process.env.NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY === "string" &&
  process.env.NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY.length > 0;

function DevModeBadge() {
  return (
    <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">
      dev mode
    </span>
  );
}

// Lazy chunk containing the Stack-dependent UI. Only loaded when Stack is
// configured so unauth'd dev builds never import the Stack hooks module.
const StackUserMenu = React.lazy(() => import("./user-menu-stack"));

export function UserMenu() {
  if (!stackConfigured) {
    return (
      <Link
        href="/profile"
        className={cn("flex items-center gap-2 text-xs px-2 py-1 rounded-md border bg-background/60 hover:bg-accent")}
        title="Open profile"
      >
        <UserRound className="h-3.5 w-3.5 text-muted-foreground" />
        <DevModeBadge />
      </Link>
    );
  }
  return (
    <React.Suspense fallback={<span className="h-8 w-24 rounded-md bg-muted/30 animate-pulse" />}>
      <StackUserMenu />
    </React.Suspense>
  );
}

// Re-export a tiny signed-out fallback the StackUserMenu can use.
export function SignOutButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="ghost" size="sm" onClick={onClick} className="h-8 gap-1.5">
      <LogOut className="h-3.5 w-3.5" />
      <span className="text-xs">Sign out</span>
    </Button>
  );
}
