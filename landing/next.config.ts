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
      /* /getting-started moved out of /docs in v0.12 — it is a setup wizard,
       * not reference material, so it sits at the top level next to /cli. */
      { source: "/docs/getting-started", destination: "/getting-started", permanent: false },
      /* CLI moved to top-level `/cli` in v0.10. */
      { source: "/docs/shipctl", destination: "/docs/configuration", permanent: false },
      { source: "/docs/shipctl/:path*", destination: "/docs/configuration", permanent: false },
      { source: "/cli", destination: "/docs/configuration", permanent: false },
      { source: "/cli/:path*", destination: "/docs/configuration", permanent: false },
      /* Old MkDocs paths a few external docs link to. */
      { source: "/docs/tools/shipctl-cli", destination: "/docs/configuration", permanent: false },
      { source: "/docs/tools/ship-agent-trackers", destination: "/tools", permanent: false },
      { source: "/docs/tools/ship-agent-ci", destination: "/tools", permanent: false },
      /* Docs reorganisation — v0.11. */
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
      { source: "/tools", destination: "/docs", permanent: false },
      { source: "/tools/:path*", destination: "/docs", permanent: false },
      { source: "/collections", destination: "/getting-started", permanent: false },
      { source: "/collections/:path*", destination: "/getting-started", permanent: false },
      { source: "/use-cases/ship", destination: "/use-cases", permanent: false },
    ];
  },
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "api.together.ai", pathname: "/**" },
      { protocol: "https", hostname: "**.together.ai", pathname: "/**" },
      { protocol: "https", hostname: "avatars.githubusercontent.com", pathname: "/u/**" },
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
