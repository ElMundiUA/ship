import type { NextConfig } from "next";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  async redirects() {
    return [
      { source: "/docs/framework", destination: "/book", permanent: false },
      { source: "/docs/framework/", destination: "/book", permanent: false },
      /* Convenience aliases — people type /cli and /getting-started in the bar. */
      { source: "/cli", destination: "/docs/shipctl", permanent: false },
      { source: "/cli/:path*", destination: "/docs/shipctl", permanent: false },
      { source: "/getting-started", destination: "/docs/getting-started", permanent: false },
      /* Old MkDocs paths a few external docs link to. */
      { source: "/docs/tools/shipctl-cli", destination: "/docs/shipctl", permanent: false },
      { source: "/docs/tools/ship-agent-trackers", destination: "/tools", permanent: false },
      { source: "/docs/tools/ship-agent-ci", destination: "/tools", permanent: false },
    ];
  },
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "api.together.ai", pathname: "/**" },
      { protocol: "https", hostname: "**.together.ai", pathname: "/**" },
    ],
  },
  outputFileTracingIncludes: {
    "/book": [
      path.join(__dirname, "content", "book.md"),
      path.join(__dirname, "public", "diagrams", "architecture.svg"),
      path.join(__dirname, "public", "diagrams", "sdlc-linear-states.svg"),
    ],
  },
};

export default nextConfig;
