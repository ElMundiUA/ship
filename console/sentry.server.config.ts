/**
 * Sentry initialisation for the Next.js server runtime (RSC, route handlers,
 * server actions). Mirrored by `sentry.edge.config.ts` for the edge runtime
 * and by `instrumentation-client.ts` for the browser bundle.
 *
 * Empty `SENTRY_DSN` keeps the SDK quiet on a developer laptop — important
 * because we ship the same Docker image with Sentry wired up to all three
 * targets (laptop / self-hosted / SaaS) and only the last one usually has a
 * project to report into.
 */

import * as Sentry from "@sentry/nextjs";

const dsn = process.env.SENTRY_DSN ?? process.env.NEXT_PUBLIC_SENTRY_DSN ?? "";

if (dsn) {
  Sentry.init({
    dsn,
    environment:
      process.env.SENTRY_ENVIRONMENT ??
      process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ??
      "local",
    tracesSampleRate: Number(
      process.env.SENTRY_TRACES_SAMPLE_RATE ??
        process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE ??
        "0",
    ),
    sendDefaultPii: false,
    release: process.env.SHIP_VERSION
      ? `ship@${process.env.SHIP_VERSION}`
      : undefined,
    initialScope: { tags: { service: "ship-console-server" } },
  });
}
