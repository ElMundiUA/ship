/**
 * Demo-recording config — extends the project's default config and
 * forces video + trace capture for every run. Use ad-hoc:
 *
 *   npx playwright test --config=playwright.demo.config.ts <spec>
 *
 * Output lands under ``test-results/<test-name>/`` as ``video.webm``
 * (and ``trace.zip`` if the run touches the browser long enough to
 * flush the trace buffer).
 */

import { defineConfig } from "@playwright/test";

import base from "./playwright.config";

export default defineConfig({
  ...base,
  // Single worker keeps the demo recording linear (no parallel
  // browser tabs interleaved across videos).
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    ...(base.use ?? {}),
    video: "on",
    trace: "on",
    screenshot: "on",
    // Slightly larger viewport so the demo reads well on share.
    viewport: { width: 1440, height: 900 },
    launchOptions: {
      // Add a small per-action delay so the recording is watchable.
      // Override via E2E_DEMO_SLOWMO=N when you want a faster/slower
      // capture (default 350ms reads well on screen-share playback).
      slowMo: Number.parseInt(process.env.E2E_DEMO_SLOWMO ?? "350", 10),
    },
  },
});
