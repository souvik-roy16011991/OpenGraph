"use client";

import * as React from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";

export function RouteProgress() {
  const pathname = usePathname();
  const search = useSearchParams();
  const [loading, setLoading] = React.useState(false);
  const prev = React.useRef({ pathname, search: search?.toString() ?? "" });

  // End when the URL actually changes (navigation commit).
  React.useEffect(() => {
    const current = { pathname, search: search?.toString() ?? "" };
    if (current.pathname !== prev.current.pathname || current.search !== prev.current.search) {
      prev.current = current;
      const t = setTimeout(() => setLoading(false), 250);
      return () => clearTimeout(t);
    }
  }, [pathname, search]);

  // Start on internal link clicks.
  React.useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      const target = e.target as Element | null;
      const a = target?.closest?.("a");
      if (!a) return;
      const href = a.getAttribute("href");
      if (!href || href.startsWith("#") || a.getAttribute("target") === "_blank") return;
      const isInternal =
        href.startsWith("/") || href.startsWith(window.location.origin);
      if (!isInternal) return;
      const destUrl = new URL(href, window.location.origin);
      if (
        destUrl.pathname === window.location.pathname &&
        destUrl.search === window.location.search
      ) {
        return;
      }
      setLoading(true);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);

  // Safety: auto-dismiss after 8s so a failed navigation doesn't leave the bar stuck.
  React.useEffect(() => {
    if (!loading) return;
    const t = setTimeout(() => setLoading(false), 8000);
    return () => clearTimeout(t);
  }, [loading]);

  return (
    <div
      aria-hidden
      className={cn(
        "fixed top-0 left-0 right-0 z-[100] h-[2px] pointer-events-none transition-opacity duration-200",
        loading ? "opacity-100" : "opacity-0",
      )}
    >
      <div
        key={loading ? "on" : "off"}
        className={cn(
          "h-full bg-gradient-to-r from-primary via-primary to-primary/60",
          loading && "animate-route-progress",
        )}
      />
    </div>
  );
}
