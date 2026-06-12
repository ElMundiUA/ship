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
  // NOTE: the pre-auth-era `/api/:path*` → `${SHIP_API_URL}/v1/:path*`
  // rewrite was removed 2026-06-13. Every browser call goes through a
  // real route handler now (`/api/<feature>` BFFs or the
  // `/api/v1/[...path]` catch-all proxy, which attaches the session
  // cookie). The rewrite was not just dead — in `next dev` afterFiles
  // rewrites are evaluated before lazily-compiled dynamic app routes,
  // so it shadowed every dynamic `/api` handler (inbox dispositions,
  // the v1 catch-all) and sent their requests to the backend
  // unauthenticated. Production builds were unaffected.
  // RFC-0010 URL migration (Plays/Inbox redesign — P1-05..P1-08, P2-17,
  // P2-18). Each entry maps a legacy URL to its new home. Order
  // matters: more-specific ``has`` matches MUST come before the
  // generic same-source rule (Next walks the array top-down and stops
  // at the first hit). Every entry is ``permanent: true`` (HTTP 308 /
  // 301 semantics) — these moves are not coming back. Param-aware
  // redirects that need a server-side lookup (slug → repo_id) live in
  // dedicated redirect-page files under ``src/app`` instead.
  async redirects() {
    return [
      // ----- P1-08: fleet/policy moved -----------------------------
      {
        source: "/fleet/policy",
        destination: "/settings/policy",
        permanent: true,
      },
      {
        source: "/fleet/policy/:rest*",
        destination: "/settings/policy/:rest*",
        permanent: true,
      },
      // ----- Sprint B: redirect-only pages collapsed -----------------
      // /members and /integrations were 30-line page.tsx files that
      // parsed query params and redirected into the settings mega-page.
      // Next preserves query strings on redirects by default, so these
      // are declarative rules now.
      {
        source: "/members",
        destination: "/settings?tab=members",
        permanent: true,
      },
      {
        source: "/integrations",
        destination: "/settings?tab=integrations",
        permanent: true,
      },
    ];
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
