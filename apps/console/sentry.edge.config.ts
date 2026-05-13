/**
 * Sentry initialisation for the Edge runtime (middleware + edge route
 * handlers). Kept symmetrical with `sentry.server.config.ts`; the only
 * meaningful difference is the `service` tag so events from middleware are
 * easy to filter in the Sentry UI.
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
    initialScope: { tags: { service: "ship-console-edge" } },
  });
}
