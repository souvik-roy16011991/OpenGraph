"use client";

import * as React from "react";

/**
 * Dev-time diagnostic for the notorious "[object Event]" error.
 *
 * A plain ``[object Event]`` in the Next.js dev overlay means *something*
 * threw or rejected with a DOM ``Event`` instead of an ``Error``. The
 * overlay stringifies non-Error values poorly, so the stack frame and
 * origin vanish. Common culprits:
 *
 *   - A ``<script>`` / ``<link>`` / ``<img>`` / ``<video>`` fires its
 *     ``error`` event (network / CORS / ad-blocker).
 *   - A ``Promise`` rejects with an Event (e.g. MediaRecorder, EventSource).
 *   - HMR / Webpack dev-overlay websocket blips in Next.js 15.
 *
 * This listener runs once on mount. When an otherwise-opaque Event fires,
 * it prints a structured record to the console so the next occurrence
 * points at the actual DOM node / URL / rejection reason — no more
 * guessing which page or component caused it.
 *
 * Production behaviour: still installed (harmless), but quieter — it
 * only logs when ``process.env.NODE_ENV !== "production"``. Errors that
 * real users hit go through the existing toast + React error boundaries;
 * this hook is purely a developer aid.
 */
export function GlobalErrorLogger() {
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const dev = process.env.NODE_ENV !== "production";

    function describeTarget(t: EventTarget | null): string {
      if (!(t instanceof Element)) return "(no element target)";
      const tag = t.tagName.toLowerCase();
      const src =
        (t as HTMLImageElement).src ||
        (t as HTMLScriptElement).src ||
        (t as HTMLLinkElement).href ||
        "";
      const id = t.id ? `#${t.id}` : "";
      const cls = t.className ? `.${String(t.className).split(" ").join(".")}` : "";
      return `<${tag}${id}${cls}${src ? `  src=${src}` : ""}>`;
    }

    // Window-level 'error' — caught uncaught JS exceptions AND failed
    // resource loads (img / script / link). Resource errors don't bubble
    // by default, hence ``capture: true``.
    const onError = (ev: ErrorEvent | Event) => {
      if (!dev) return;
      const e = ev as ErrorEvent;
      if (e.error instanceof Error) {
        // Real Error — the overlay will format this fine, nothing to add.
        return;
      }
      console.groupCollapsed(
        `%c[GlobalErrorLogger] window.error — non-Error value caught`,
        "color:#b91c1c;font-weight:bold",
      );
      console.log("type         :", ev.type);
      console.log("target       :", describeTarget(ev.target));
      console.log("message      :", e.message ?? "(no message)");
      console.log("filename     :", e.filename ?? "(unknown)");
      console.log("line:col     :", e.lineno != null ? `${e.lineno}:${e.colno}` : "(unknown)");
      console.log("raw event    :", ev);
      console.groupEnd();
    };

    const onReject = (ev: PromiseRejectionEvent) => {
      if (!dev) return;
      const r = ev.reason;
      if (r instanceof Error) {
        // The dev overlay handles these properly. Let it.
        return;
      }
      console.groupCollapsed(
        `%c[GlobalErrorLogger] unhandledrejection — non-Error reason`,
        "color:#b91c1c;font-weight:bold",
      );
      console.log("reason type  :", Object.prototype.toString.call(r));
      if (r instanceof Event) {
        console.log("event.type   :", r.type);
        console.log("event.target :", describeTarget(r.target));
      } else {
        console.log("reason value :", r);
      }
      console.log("promise      :", ev.promise);
      console.groupEnd();
    };

    window.addEventListener("error", onError, true);
    window.addEventListener("unhandledrejection", onReject);
    return () => {
      window.removeEventListener("error", onError, true);
      window.removeEventListener("unhandledrejection", onReject);
    };
  }, []);

  return null;
}
