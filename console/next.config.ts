import path from "node:path";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

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

// Wrapping with Sentry adds the build-time integration (source maps,
// release tagging, server-side instrumentation hook). All the runtime
// behaviour still comes from `sentry.{server,edge}.config.ts` and
// `instrumentation-client.ts`. The wrap is a no-op when SENTRY_DSN is
// empty; we still invoke it unconditionally so prod builds don't pick up
// a different config tree than local builds.
const sentryBuildOptions = {
  // Operator silence: don't print Sentry's giant build banner every time
  // someone runs `next build`. Errors and warnings still surface.
  silent: true,
  // Hide the wrapped __sentry_release file from the public bundle. Not
  // strictly necessary but matches what the Sentry wizard generates.
  hideSourceMaps: true,
  // We have no Sentry CLI auth token in the default local build; the
  // wrapper degrades gracefully in that case.
  disableLogger: true,
};

export default withSentryConfig(nextConfig, sentryBuildOptions);
