import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  async rewrites() {
    return [
      // Proxy agent.md to backend (for AI agent discovery)
      {
        source: '/agent.md',
        destination: 'http://127.0.0.1:8000/agent.md',
      },
      // Generic API Proxy: /api/* -> backend /*
      // Frontend pages use /api/repos, /api/stats etc.
      // This strips the /api prefix when forwarding to backend
      {
        source: '/api/:path*',
        destination: 'http://127.0.0.1:8000/:path*',
      }
    ];
  },
};

export default nextConfig;
