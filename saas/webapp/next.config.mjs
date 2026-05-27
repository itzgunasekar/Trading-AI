/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Proxy /api/* to FastAPI control plane during development.
  // In production, point this at your deployed control plane URL.
  async rewrites() {
    const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${API}/:path*` }];
  },
  // Security headers — applied on top of FastAPI's own headers
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options",        value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy",        value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy",     value: "geolocation=(), microphone=(), camera=()" },
        ],
      },
    ];
  },
};
export default nextConfig;
