import path from "node:path";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  // `standalone` keeps the production Docker image small (~150 MB) by copying
  // only the deps Next actually traced. The compose `console` service relies
  // on this — it runs `node server.js` from /app/.next/standalone.
  output: "standalone",
  // Pin tracing root so Next stops warning about the repo-root lockfile
  // shared with landing/ and the Python backend.
  outputFileTracingRoot: __dirname,
  // The console talks to the Ship backend (FastAPI) via /api proxy in dev.
  // Wire this up once auth/session lands. For now everything renders from
  // in-memory mock data inside src/lib/mock/.
  async rewrites() {
    const backend = process.env.SHIP_API_URL;
    if (!backend) return [];
    return [{ source: "/api/:path*", destination: `${backend}/v1/:path*` }];
  },
};

export default nextConfig;
