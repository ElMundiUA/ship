import type { NextConfig } from "next";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  async redirects() {
    return [
      { source: "/docs/framework", destination: "/book", permanent: false },
      { source: "/docs/framework/", destination: "/book", permanent: false },
      { source: "/docs/framework/:path*", destination: "/book", permanent: false },
      /* Convenience alias — people type /getting-started in the bar. */
      { source: "/getting-started", destination: "/docs/getting-started", permanent: false },
      /* CLI moved to top-level `/cli` in v0.10. */
      { source: "/docs/shipctl", destination: "/cli", permanent: false },
      { source: "/docs/shipctl/:path*", destination: "/cli", permanent: false },
      /* Old MkDocs paths a few external docs link to. */
      { source: "/docs/tools/shipctl-cli", destination: "/cli", permanent: false },
      { source: "/docs/tools/ship-agent-trackers", destination: "/tools", permanent: false },
      { source: "/docs/tools/ship-agent-ci", destination: "/tools", permanent: false },
      /* Manual reorganisation — v0.11. */
      { source: "/docs/adoption", destination: "/docs", permanent: false },
      { source: "/docs/adoption/agent-playbook", destination: "/docs/operating", permanent: false },
      { source: "/docs/adoption/elmundi", destination: "/use-cases", permanent: false },
      { source: "/docs/adoption/delivery-quality-and-release-process", destination: "/docs/operating", permanent: false },
      { source: "/docs/adoption/agent-setup-contract", destination: "/docs/discovery", permanent: false },
      { source: "/docs/adoption/agent-launch-matrix", destination: "/docs/agent-matrix", permanent: false },
      { source: "/docs/adoption/:path*", destination: "/docs", permanent: false },
      { source: "/docs/prompts-workflows", destination: "/patterns", permanent: false },
      { source: "/docs/prompts-workflows/:path*", destination: "/patterns", permanent: false },
      { source: "/docs/examples/elmundi", destination: "/use-cases/elmundi", permanent: false },
      { source: "/docs/examples/elmundi/:path*", destination: "/use-cases/elmundi", permanent: false },
      { source: "/docs/examples/:path*", destination: "/use-cases", permanent: false },
      { source: "/docs/legal-copyright", destination: "/docs/legal", permanent: false },
      { source: "/docs/rfc", destination: "/docs/protocol", permanent: false },
      { source: "/docs/rfc/:path*", destination: "/docs/protocol/:path*", permanent: false },
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
