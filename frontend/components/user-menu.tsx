"use client";

import * as React from "react";
import Link from "next/link";
import { LogIn, LogOut, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Header user menu.
 *
 * - Stack Auth ON  → lazy-load the Stack SDK and show signed-in user +
 *   sign-out (or a "Sign in" button when logged out).
 * - Stack Auth OFF → show a "dev mode" chip linking to /profile AND a
 *   prominent "Sign in" button that goes to /sign-in (a smart page that
 *   redirects to Stack's handler when configured, or explains the dev-mode
 *   state otherwise).
 *
 * The intent is that sign-in is always *visible* — never silently hidden
 * just because env vars are missing.
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
        <Button asChild variant="outline" size="sm" className="h-8 gap-1.5">
          <Link href="/sign-in">
            <LogIn className="h-3.5 w-3.5" />
            <span className="text-xs">Sign in</span>
          </Link>
        </Button>
      </div>
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
