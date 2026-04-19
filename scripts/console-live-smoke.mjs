#!/usr/bin/env node
/**
 * Drives the operator console through the live signup → catalog path:
 *
 *   1. Open /login
 *   2. Switch to "Create account"
 *   3. Sign up with a fresh tour-* email
 *   4. Land on dashboard, then visit /catalog
 *   5. Need to first create a workspace via API (UI onboarding is still mock).
 *   6. Reload /catalog and screenshot the live state.
 *
 * Output:
 *   output/playwright/live-login.png
 *   output/playwright/live-catalog.png
 */

import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const OUT_DIR = path.join(REPO_ROOT, "output", "playwright");

const BASE_URL = process.env.BASE_URL ?? "http://localhost:3001";
const API_URL = process.env.API_URL ?? "http://localhost:8100";
const VIEWPORT = { width: 1440, height: 900 };

async function ensureDir(dir) {
  if (!existsSync(dir)) await mkdir(dir, { recursive: true });
}

async function main() {
  await ensureDir(OUT_DIR);
  const email = `tour-${Date.now()}@ship.dev`;
  const password = "ship-tour-pass-1234";
  console.log(`[smoke] base=${BASE_URL} api=${API_URL} email=${email}`);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: VIEWPORT });
  const page = await context.newPage();

  await page.goto(`${BASE_URL}/login`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("load");
  await page.screenshot({ path: path.join(OUT_DIR, "live-login.png") });

  await page.getByRole("button", { name: "Create an account" }).click();
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="password"]', password);
  await page.fill('input[name="display_name"]', "Tour User");
  await Promise.all([
    page.waitForURL(`${BASE_URL}/`, { timeout: 15000 }),
    page.getByRole("button", { name: /Create account/i }).click(),
  ]);
  console.log("[smoke] signed up + landed on /");
  await page.screenshot({ path: path.join(OUT_DIR, "live-dashboard.png"), fullPage: true });

  // First catalog visit: still mock, since the new user has zero workspaces.
  await page.goto(`${BASE_URL}/catalog`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("load");
  await page.screenshot({
    path: path.join(OUT_DIR, "live-catalog-no-ws.png"),
    fullPage: true,
  });

  // Pull the session cookie and create a workspace via the API directly,
  // since the onboarding wizard hasn't been wired yet.
  // After the form-driven login, Chromium needs a beat to commit Set-Cookie
  // from the 303 response to the on-disk jar.
  await page.waitForTimeout(300);
  const cookies = await context.cookies(BASE_URL);
  const session = cookies.find((c) => c.name === "ship_session");
  if (!session) {
    console.log("[smoke] cookies:", JSON.stringify(cookies, null, 2));
    throw new Error("ship_session cookie was not set after signup");
  }
  const wsRes = await fetch(`${API_URL}/v1/workspaces`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${session.value}`,
    },
    body: JSON.stringify({ name: "Tour Workspace", slug: "tour-live" }),
  });
  if (!wsRes.ok) {
    throw new Error(`create workspace failed: ${wsRes.status} ${await wsRes.text()}`);
  }
  const ws = await wsRes.json();
  console.log(`[smoke] created workspace ${ws.slug} (${ws.id})`);

  await page.goto(`${BASE_URL}/catalog`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("load");
  await page.waitForSelector("text=Reading from", { timeout: 10000 });
  await page.screenshot({
    path: path.join(OUT_DIR, "live-catalog.png"),
    fullPage: true,
  });
  console.log("[smoke] live-catalog screenshot saved");

  await page.close();
  await context.close();
  await browser.close();
  console.log("[smoke] done");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
