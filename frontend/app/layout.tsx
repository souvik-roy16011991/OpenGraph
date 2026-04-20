import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { AppShell } from "@/components/app-shell";
import { StackProvider, StackTheme } from "@stackframe/stack";
import { stackServerApp, neonAuthEnabled } from "@/stack";

export const metadata: Metadata = {
  title: "OpenGraph",
  description:
    "OpenGraph — enterprise knowledge graph builder. Pick a template, upload " +
    "your KB JSONs, and chat with a typed, per-workspace graph.",
  icons: {
    icon: [
      { url: "/favicon.svg", type: "image/svg+xml" },
    ],
  },
};

// Every page is heavily client-state driven (zustand, react-query, localStorage,
// Cytoscape). Opt out of static prerender so `next build` doesn't try to
// evaluate browser-only code at build time, and `next start` never serves a
// stale snapshot that disagrees with the client.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // If Neon Auth env vars aren't set, skip the provider so the app still
  // boots; protected routes will 503 until the deployment is configured.
  const content = (
    <Providers>
      <AppShell>{children}</AppShell>
    </Providers>
  );

  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen">
        {neonAuthEnabled && stackServerApp ? (
          <StackProvider app={stackServerApp}>
            <StackTheme>{content}</StackTheme>
          </StackProvider>
        ) : (
          content
        )}
      </body>
    </html>
  );
}
