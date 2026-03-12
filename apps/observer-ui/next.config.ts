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
      // API v1 routes: preserve /api/v1 prefix (agent_auth routes)
      {
        source: '/api/v1/:path*',
        destination: 'http://127.0.0.1:8000/api/v1/:path*',
      },
      // Generic API Proxy: /api/* -> backend /* (strips prefix)
      // For routes like /repos, /bounties, /stats
      {
        source: '/api/:path*',
        destination: 'http://127.0.0.1:8000/:path*',
      }
    ];
  },
};

export default nextConfig;
