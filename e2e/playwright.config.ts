import { defineConfig, devices } from "@playwright/test";

/**
 * Base URL for the operator console (Next on port 3001 locally).
 * Staging/prod: set in CI secrets.
 */
const baseURL =
  process.env.E2E_CONSOLE_BASE_URL?.replace(/\/$/, "") ||
  "http://127.0.0.1:3001";

const storageState = process.env.E2E_STORAGE_STATE?.trim();

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI
    ? [["html", { open: "never" }], ["github"], ["line"]]
    : [["html"], ["line"]],
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
  projects: [
    {
      name: "public",
      testMatch: /.*\.public\.spec\.ts/,
    },
    {
      name: "authenticated",
      testMatch: /.*\.wired\.spec\.ts/,
      ...(storageState ? { use: { storageState } } : {}),
    },
    {
      name: "sandbox-api",
      testMatch: /.*\.sandbox\.spec\.ts/,
    },
    /** Без cookies — только когда E2E_STORAGE_STATE не задан глобально. */
    {
      name: "noauth",
      testMatch: /.*\.noauth\.spec\.ts/,
      use: { storageState: { cookies: [], origins: [] } },
    },
  ],
});
