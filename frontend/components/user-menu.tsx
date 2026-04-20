"use client";

import * as React from "react";
import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * Header user menu.
 *
 * Auth is always enforced (see middleware.ts), so by the time this
 * component mounts the user has a valid Neon Auth session; the lazy-loaded
 * <StackUserMenu> shows their email + a sign-out button backed by the
 * @stackframe/stack client SDK (pointed at the Neon Auth tenant).
 */

const StackUserMenu = React.lazy(() => import("./user-menu-stack"));

export function UserMenu() {
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
