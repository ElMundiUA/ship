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
      // ----- P1-05: workspace top-level renames --------------------
      // ``/lanes?tab=library`` is the catalog tab → folds into Plays.
      // Listed BEFORE the generic ``/lanes`` rule so the specific
      // ``has`` match wins.
      {
        source: "/lanes",
        has: [{ type: "query", key: "tab", value: "library" }],
        destination: "/plays",
        permanent: true,
      },
      // Everything else under /lanes (including ``?tab=active``) maps
      // 1:1 to /automations. Next preserves the rest of the query
      // string by default.
      { source: "/lanes", destination: "/automations", permanent: true },
      {
        source: "/lanes/:id",
        destination: "/automations/:id",
        permanent: true,
      },
      { source: "/pipelines", destination: "/runs", permanent: true },
      // Legacy run-detail URL embedded both pipelineId and runId; the
      // new shape is keyed off runId alone.
      {
        source: "/pipelines/:pipelineId/runs/:runId",
        destination: "/runs/:runId",
        permanent: true,
      },
      // /requests = the catalog form, which folded into /plays.
      { source: "/requests", destination: "/plays", permanent: true },

      // ----- P1-06: fleet/{lanes,requests} retired -----------------
      // Fleet surface collapses into a ``?scope=fleet`` query on the
      // workspace pages. Catch-alls drop sub-routes like ``new`` /
      // ``[id]`` since v1 doesn't preserve them.
      {
        source: "/fleet/lanes",
        destination: "/automations?scope=fleet",
        permanent: true,
      },
      {
        source: "/fleet/lanes/:rest*",
        destination: "/automations?scope=fleet",
        permanent: true,
      },
      {
        source: "/fleet/requests",
        destination: "/runs?scope=fleet",
        permanent: true,
      },
      {
        source: "/fleet/requests/:rest*",
        destination: "/runs?scope=fleet",
        permanent: true,
      },

      // ----- P1-07: per-repo clarifications/improvements/feedback --
      // The lanes/requests per-repo redirects need a slug→repo_id
      // lookup, so they live in dedicated redirect-pages
      // (``src/app/r/[owner]/[repo]/{lanes,requests}/page.tsx``).
      // The three below don't need the lookup — they fold into
      // workspace surfaces that already understand cross-repo data.
      {
        source: "/r/:owner/:repo/clarifications",
        destination: "/inbox?type=clarification",
        permanent: true,
      },
      {
        source: "/r/:owner/:repo/clarifications/:id",
        destination: "/inbox?type=clarification&legacy_id=:id",
        permanent: true,
      },
      {
        source: "/r/:owner/:repo/improvements",
        destination: "/inbox?type=improvement",
        permanent: true,
      },
      {
        source: "/r/:owner/:repo/improvements/:id",
        destination: "/inbox?type=improvement&legacy_id=:id",
        permanent: true,
      },
      {
        source: "/r/:owner/:repo/artifact-feedback",
        destination: "/settings/catalog-feedback",
        permanent: true,
      },

      // ----- P1-08: fleet/policy moved, fleet/adoption gone --------
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
      // Coverage tab on /automations isn't built yet (P4-05). Per
      // planning, acceptable to redirect to the (currently 404)
      // anchor — this is a placeholder.
      {
        source: "/fleet/adoption",
        destination: "/automations?tab=coverage",
        permanent: true,
      },

      // ----- P2-17: clarifications + improvements → unified inbox --
      // Legacy clarification/improvement IDs do NOT match inbox_item
      // IDs (mirrored via ``source_table`` + ``source_id`` server-
      // side). v1 drops them on the inbox list page with a
      // ``legacy_id`` hint so the list can show a "this moved" banner;
      // proper id-to-id mapping is a follow-up.
      {
        source: "/clarifications",
        destination: "/inbox?type=clarification",
        permanent: true,
      },
      {
        source: "/clarifications/:id",
        destination: "/inbox?type=clarification&legacy_id=:id",
        permanent: true,
      },
      {
        source: "/improvements",
        destination: "/inbox?type=improvement",
        permanent: true,
      },
      {
        source: "/improvements/:id",
        destination: "/inbox?type=improvement&legacy_id=:id",
        permanent: true,
      },

      // ----- P2-18: artifact-feedback → admin-only settings page ---
      {
        source: "/artifact-feedback",
        destination: "/settings/catalog-feedback",
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
