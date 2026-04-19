import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { AppShell } from "@/components/app-shell";

export const metadata: Metadata = {
  title: "KB Knowledge Graph Engine",
  description: "Enterprise knowledge graph builder for domain + tool KBs",
};

// Every page is heavily client-state driven (zustand, react-query, localStorage,
// Cytoscape). Opt out of static prerender so `next build` doesn't try to
// evaluate browser-only code at build time, and `next start` never serves a
// stale snapshot that disagrees with the client.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
