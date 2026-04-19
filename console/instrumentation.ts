/**
 * Next.js instrumentation hook (App Router). Picks the right Sentry config
 * based on the runtime so the SDK is initialised once per process and never
 * loaded into bundles where it does not belong.
 *
 * Required by `@sentry/nextjs` v8+; the SDK falls back to a noop without
 * this entry point being present.
 */

export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  }
  if (process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.edge.config");
  }
}

export async function onRequestError(
  ...args: Parameters<typeof import("@sentry/nextjs").captureRequestError>
) {
  const Sentry = await import("@sentry/nextjs");
  return Sentry.captureRequestError(...args);
}
