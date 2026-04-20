"use client";

import * as React from "react";
import Link from "next/link";
import { LogOut, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { endDevSession } from "@/lib/session-actions";

/**
 * Header user menu.
 *
 * Auth is always enforced (see middleware.ts), so by the time this
 * component mounts the user has one of two valid sessions:
 *
 * - Stack Auth cookie → the lazy-loaded <StackUserMenu> shows their email +
 *   a sign-out button backed by the Stack SDK.
 * - Dev-mode cookie (`og-session=dev`) → "dev mode" chip + a sign-out
 *   action that clears the cookie and bounces back to /sign-in.
 *
 * The component decides which branch to render by the presence of the
 * NEXT_PUBLIC_* Stack env vars at build time.
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

// Lazy-load the Stack SDK chunk only when configured.
const StackUserMenu = React.lazy(() => import("./user-menu-stack"));

export function UserMenu() {
  if (!stackConfigured) {
    return (
      <div className="flex items-center gap-2">
        <Link
          href="/profile"
          className={cn(
            "flex items-center gap-2 text-xs px-2 py-1 rounded-md border bg-background/60 hover:bg-accent",
          )}
          title="Open profile (dev user)"
        >
          <UserRound className="h-3.5 w-3.5 text-muted-foreground" />
          <DevModeBadge />
        </Link>
        <form action={endDevSession}>
          <Button type="submit" variant="ghost" size="sm" className="h-8 gap-1.5" title="End dev session">
            <LogOut className="h-3.5 w-3.5" />
            <span className="text-xs">Sign out</span>
          </Button>
        </form>
      </div>
    );
  }
  return (
    <React.Suspense fallback={<span className="h-8 w-24 rounded-md bg-muted/30 animate-pulse" />}>
      <StackUserMenu />
    </React.Suspense>
  );
}

// Legacy export retained for callers still importing it.
export function SignOutButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="ghost" size="sm" onClick={onClick} className="h-8 gap-1.5">
      <LogOut className="h-3.5 w-3.5" />
      <span className="text-xs">Sign out</span>
    </Button>
  );
}
