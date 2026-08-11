/** @type {import('next').NextConfig} */
//
// Local dev only. Once NEXT_PUBLIC_API_URL is set (Vercel production/preview), the
// API client in src/lib/api.ts calls the backend at that absolute URL directly from
// the browser instead -- routing large file uploads through a Vercel rewrite would
// hit the platform's serverless body-size limit, so this proxy is deliberately a
// local-only convenience, not the production path.
const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";
const useLocalProxy = !process.env.NEXT_PUBLIC_API_URL;

const nextConfig = {
  async rewrites() {
    if (!useLocalProxy) return [];
    return [
      { source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` },
      { source: "/health", destination: `${BACKEND_URL}/health` },
      { source: "/v1/:path*", destination: `${BACKEND_URL}/v1/:path*` },
    ];
  },
};

module.exports = nextConfig;
