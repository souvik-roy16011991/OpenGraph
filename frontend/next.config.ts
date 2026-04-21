import type { NextConfig } from "next";

// BACKEND_URL is only used by the server-side `rewrites()` below. In
// production the client talks to the backend directly via NEXT_PUBLIC_API_BASE
// (baked into the bundle at `next build` time), so rewrites are mostly a
// dev-convenience.
//
// Render's `fromService.property: host` yields a bare hostname — prepend
// https:// when the scheme is missing so the rewrite destination is valid.
const RAW_BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";
const BACKEND_URL = /^https?:\/\//i.test(RAW_BACKEND_URL)
  ? RAW_BACKEND_URL
  : `https://${RAW_BACKEND_URL}`;

const config: NextConfig = {
  reactStrictMode: true,
  // ESLint / TS errors don't block production deploys — the `typecheck` and
  // `lint` scripts remain available for CI. Keeps Render builds from failing
  // on lint-only issues.
  eslint: { ignoreDuringBuilds: true },
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${BACKEND_URL}/api/v1/:path*` },
      { source: "/health", destination: `${BACKEND_URL}/health` },
    ];
  },
};

export default config;
