import type { NextConfig } from "next";

const API_BASE = process.env.BACKEND_URL || "http://localhost:8000";

const config: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${API_BASE}/api/v1/:path*` },
      { source: "/health", destination: `${API_BASE}/health` },
    ];
  },
};

export default config;
