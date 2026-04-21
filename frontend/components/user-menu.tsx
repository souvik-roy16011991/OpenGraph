"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LogOut, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getStoredUser, logout, type StoredUser } from "@/lib/auth";

/**
 * Header user menu.
 *
 * Auth is always enforced (see middleware.ts), so by the time this component
 * mounts the user has a valid JWT in localStorage/cookie. We read the stored
 * user record for a label; sign-out clears the session and redirects.
 */
export function UserMenu() {
  const router = useRouter();
  const [user, setUser] = React.useState<StoredUser | null>(null);
  const [hydrated, setHydrated] = React.useState(false);

  React.useEffect(() => {
    setUser(getStoredUser());
    setHydrated(true);
  }, []);

  if (!hydrated) {
    return <span className="h-8 w-24 rounded-md bg-muted/30 animate-pulse" />;
  }

  const label = user?.display_name || user?.email || "signed in";

  const onSignOut = () => {
    logout();
    router.replace("/sign-in");
    router.refresh();
  };

  return (
    <div className="flex items-center gap-2">
      <Link
        href="/profile"
        className="flex items-center gap-1.5 text-xs px-2 py-1 rounded-md border bg-background/60 hover:bg-accent"
        title="Open profile"
      >
        <UserRound className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="max-w-[160px] truncate">{label}</span>
      </Link>
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8"
        onClick={onSignOut}
        aria-label="Sign out"
      >
        <LogOut className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

export function SignOutButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="ghost" size="sm" onClick={onClick} className="h-8 gap-1.5">
      <LogOut className="h-3.5 w-3.5" />
      <span className="text-xs">Sign out</span>
    </Button>
  );
}
