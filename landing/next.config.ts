import type { NextConfig } from "next";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  async redirects() {
    return [
      { source: "/docs/framework", destination: "/book", permanent: false },
      { source: "/docs/framework/", destination: "/book", permanent: false },
      /* Convenience alias — people type /getting-started in the bar. */
      { source: "/getting-started", destination: "/docs/getting-started", permanent: false },
      /* CLI moved to top-level `/cli` in v0.10. The `/cli/:path*` rule keeps
       * the door open for future sub-routes (e.g. /cli/reference) without
       * shadowing the real `/cli` page. */
      { source: "/docs/shipctl", destination: "/cli", permanent: false },
      { source: "/docs/shipctl/:path*", destination: "/cli", permanent: false },
      /* Old MkDocs paths a few external docs link to. */
      { source: "/docs/tools/shipctl-cli", destination: "/cli", permanent: false },
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
