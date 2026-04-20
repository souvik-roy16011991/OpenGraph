"use client";

import { useUser, useStackApp } from "@stackframe/stack";
import { LogIn, LogOut, UserRound } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";

/**
 * Stack-SDK-dependent user menu. Split into its own module so the parent
 * UserMenu can lazy-load it — if Stack isn't configured, this file is never
 * imported, which keeps the dev bundle free of the Stack hooks.
 */

export default function StackUserMenu() {
  const user = useUser();
  const app = useStackApp();

  if (user === undefined) {
    return <span className="h-8 w-24 rounded-md bg-muted/30 animate-pulse" />;
  }

  if (user === null) {
    return (
      <Button variant="outline" size="sm" asChild className="h-8 gap-1.5">
        <Link href="/handler/sign-in">
          <LogIn className="h-3.5 w-3.5" />
          <span className="text-xs">Sign in</span>
        </Link>
      </Button>
    );
  }

  const label = user.primaryEmail || user.displayName || "signed in";

  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-1.5 text-xs px-2 py-1 rounded-md border bg-background/60">
        <UserRound className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="max-w-[160px] truncate" title={label}>{label}</span>
      </div>
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8"
        onClick={() => {
          user.signOut();
          app.redirectToSignIn();
        }}
        aria-label="Sign out"
      >
        <LogOut className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
