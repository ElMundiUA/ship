/**
 * Vitest setup for the operator console.
 *
 * Used for fast unit / component tests of React surfaces that don't
 * need a real browser — tool renderers, helper libs, pure formatters.
 * Full-browser flows still live in ``e2e/`` (Playwright).
 */

import path from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";


const __dirname = path.dirname(fileURLToPath(import.meta.url));


export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // Mirror the Next.js path alias so production imports
      // (``@/components/...``) resolve in tests.
      "@": path.resolve(__dirname, "src"),
      "server-only": path.resolve(__dirname, "src/test/server-only-mock.ts"),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    globals: true,
    css: false,
  },
});
