/**
 * Browser-side Sentry initialisation. Picked up automatically by Next.js
 * 15.3+ (`instrumentation-client.{ts,js}`); for older runtimes it is also
 * imported from the root layout via a tiny client component.
 *
 * Browser bundles cannot read non-`NEXT_PUBLIC_*` env vars, so the public
 * variants below are required to opt the browser into Sentry. Set them in
 * the deploy target only when the project should report client errors.
 */

import * as Sentry from "@sentry/nextjs";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN ?? "";

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? "local",
    tracesSampleRate: Number(
      process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE ?? "0",
    ),
    // No PII by default. Identify the operator only after they sign in via
    // Auth0 — see `console/src/lib/auth0.ts`, which calls
    // `Sentry.setUser({ id, email })` once a session is established.
    sendDefaultPii: false,
    integrations: [Sentry.browserTracingIntegration()],
    release: process.env.NEXT_PUBLIC_SHIP_VERSION
      ? `ship@${process.env.NEXT_PUBLIC_SHIP_VERSION}`
      : undefined,
    initialScope: { tags: { service: "ship-console-browser" } },
  });
}

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
