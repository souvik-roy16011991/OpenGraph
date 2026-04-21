"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LogOut, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { logout } from "@/lib/auth";
import { getSupabaseBrowserClient } from "@/lib/supabase";

export function UserMenu() {
  const router = useRouter();
  const [label, setLabel] = React.useState<string>("");
  const [hydrated, setHydrated] = React.useState(false);

  React.useEffect(() => {
    getSupabaseBrowserClient()
      .auth.getUser()
      .then(({ data }) => {
        const u = data.user;
        if (u) {
          const meta = u.user_metadata ?? {};
          setLabel(
            (meta.display_name as string | null) ||
            (meta.full_name as string | null) ||
            u.email ||
            "signed in",
          );
        }
        setHydrated(true);
      });
  }, []);

  if (!hydrated) {
    return <span className="h-8 w-24 rounded-md bg-muted/30 animate-pulse" />;
  }

  const onSignOut = async () => {
    await logout();
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
        <span className="max-w-[160px] truncate">{label || "signed in"}</span>
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
