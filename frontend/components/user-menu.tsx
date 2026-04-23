"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ChevronDown,
  CircleUser,
  CreditCard,
  LogOut,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getStoredUser, logout, type StoredUser } from "@/lib/auth";

/**
 * Header user menu.
 *
 * The trigger is a compact chip (avatar + label). Clicking opens a popover
 * that groups account-scope actions (Profile, Billing, Sign out) so they
 * don't eat nav rows in the sidebar. The popover anchors bottom-right and
 * closes on outside click, Escape, or item selection.
 */
export function UserMenu() {
  const router = useRouter();
  const [user, setUser] = React.useState<StoredUser | null>(null);
  const [hydrated, setHydrated] = React.useState(false);
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    setUser(getStoredUser());
    setHydrated(true);
  }, []);

  React.useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!hydrated) {
    return <span className="h-8 w-24 rounded-md bg-muted/30 animate-pulse" />;
  }

  const label = user?.display_name || user?.email || "signed in";
  const initial = (user?.display_name || user?.email || "·").slice(0, 1).toUpperCase();

  const onSignOut = () => {
    setOpen(false);
    logout();
    router.replace("/sign-in");
    router.refresh();
  };

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="inline-flex items-center gap-2 text-xs px-2 py-1.5 rounded-md border bg-background/60 hover:bg-accent transition-colors"
      >
        <span className="h-5 w-5 rounded-full bg-primary/15 text-primary text-[10px] font-semibold flex items-center justify-center">
          {initial}
        </span>
        <span className="max-w-[140px] truncate">{label}</span>
        <ChevronDown
          className={cn(
            "h-3 w-3 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div
          role="menu"
          className={cn(
            "absolute right-0 top-full mt-1 w-56 rounded-md border bg-popover shadow-lg z-30",
            "animate-in fade-in-0 zoom-in-95 slide-in-from-top-1 duration-150",
          )}
        >
          <div className="px-3 py-2 border-b">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Signed in as</p>
            <p className="text-sm font-medium truncate" title={user?.email ?? undefined}>
              {label}
            </p>
          </div>
          <MenuItem href="/profile" icon={CircleUser} onSelect={() => setOpen(false)}>
            Profile
          </MenuItem>
          <MenuItem href="/billing" icon={CreditCard} onSelect={() => setOpen(false)}>
            Billing
          </MenuItem>
          <div className="border-t">
            <button
              type="button"
              role="menuitem"
              onClick={onSignOut}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-accent transition-colors text-left"
            >
              <LogOut className="h-3.5 w-3.5 text-muted-foreground" />
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function MenuItem({
  href,
  icon: Icon,
  children,
  onSelect,
}: {
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
  onSelect?: () => void;
}) {
  return (
    <Link
      href={href}
      role="menuitem"
      onClick={onSelect}
      className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-accent transition-colors"
    >
      <Icon className="h-3.5 w-3.5 text-muted-foreground" />
      {children}
    </Link>
  );
}

/**
 * Kept for callers that use the stand-alone sign-out button inside other
 * menus (e.g. /profile). Same handler contract as the inline menu item.
 */
export function SignOutButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="ghost" size="sm" onClick={onClick} className="h-8 gap-1.5">
      <LogOut className="h-3.5 w-3.5" />
      <span className="text-xs">Sign out</span>
    </Button>
  );
}
