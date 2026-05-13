#!/usr/bin/env node
/**
 * End-to-end smoke for the live onboarding wizard + integrations + catalog.
 *
 *   1. Open /login → switch to "Create account" → sign up
 *   2. Land on / → expect a redirect to /onboarding (no workspaces yet)
 *   3. Wizard step 1: fill name + slug → submit → expect step=integration
 *   4. Wizard step 2: pick Linear, drop a fake secret → expect step=token
 *   5. Wizard step 3: click "Generate token" → expect a secret to appear
 *   6. Wizard step 4: visit /catalog → expect LiveBanner
 *   7. Visit /integrations → expect Linear listed with "secret stored"
 *   8. Disconnect → expect it to disappear
 *
 * Screenshots land under output/playwright/ for visual review.
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
const VIEWPORT = { width: 1440, height: 900 };

async function ensureDir(dir) {
  if (!existsSync(dir)) await mkdir(dir, { recursive: true });
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(OUT_DIR, name), fullPage: true });
}

async function main() {
  await ensureDir(OUT_DIR);
  const stamp = Date.now();
  const email = `wiz-${stamp}@ship.dev`;
  const password = "ship-tour-pass-1234";
  const slug = `wiz-${stamp.toString(36)}`;
  console.log(`[smoke] base=${BASE_URL} email=${email} slug=${slug}`);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: VIEWPORT });
  const page = await context.newPage();

  // Surface backend errors so debugging is easy when the smoke breaks.
  page.on("pageerror", (e) => console.log("[pageerror]", e.message));
  page.on("response", (r) => {
    if (r.status() >= 400) console.log("[http]", r.status(), r.url());
  });

  // 1. Sign up.
  await page.goto(`${BASE_URL}/login`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("load");
  await page.getByRole("button", { name: "Create an account" }).click();
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="password"]', password);
  await page.fill('input[name="display_name"]', "Wizard User");
  await Promise.all([
    page.waitForURL(/\/(onboarding|)/, { timeout: 15000 }),
    page.getByRole("button", { name: /Create account/i }).click(),
  ]);

  // 2. We expect to be bounced to /onboarding because there are zero workspaces.
  await page.waitForURL(/\/onboarding/, { timeout: 10000 });
  console.log("[smoke] auto-redirected to onboarding");
  await shot(page, "wizard-step1.png");

  // 3. Workspace step.
  await page.fill('input[name="name"]', "Wizard Workspace");
  await page.fill('input[name="slug"]', slug);
  await Promise.all([
    page.waitForURL(/step=integration/, { timeout: 15000 }),
    page.getByRole("button", { name: /Create workspace/i }).click(),
  ]);
  console.log("[smoke] workspace created");
  await shot(page, "wizard-step2.png");

  // 4. Integration step — pick Linear (default), drop a fake secret.
  await page.fill('input[name="config_team_id"]', "ENG");
  await page.fill('input[name="secret"]', "lin_api_smoke_test_secret");
  await Promise.all([
    page.waitForURL(/step=token/, { timeout: 15000 }),
    page.getByRole("button", { name: /Save & continue/i }).click(),
  ]);
  console.log("[smoke] secret saved");
  await shot(page, "wizard-step3-before.png");

  // 5. Token step — click "Generate token" and wait for the secret to render.
  await page.getByRole("button", { name: /Generate token/i }).click();
  await page.waitForSelector("text=Personal Access Token", { timeout: 15000 });
  // Confirm the input has a non-empty value (the secret).
  const tokenValue = await page.inputValue('input[readonly]');
  if (!tokenValue || tokenValue.length < 20) {
    throw new Error(`token input looked empty: '${tokenValue}'`);
  }
  console.log(`[smoke] minted token (prefix: ${tokenValue.slice(0, 12)}…)`);
  await shot(page, "wizard-step3-after.png");

  // 6. Finish + visit catalog.
  await page.getByRole("link", { name: /I saved it · finish/i }).click();
  await page.waitForURL(/step=done/, { timeout: 10000 });
  await shot(page, "wizard-step4-done.png");
  await page.getByRole("link", { name: /Open catalog/i }).click();
  await page.waitForURL(/\/catalog/, { timeout: 10000 });
  await page.waitForSelector("text=Reading from", { timeout: 10000 });
  await shot(page, "wizard-catalog.png");
  console.log("[smoke] catalog showed live data");

  // 7. Integrations page should show Linear with "secret stored".
  await page.goto(`${BASE_URL}/integrations`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("load");
  await page.waitForSelector("text=Linear", { timeout: 10000 });
  await page.waitForSelector("text=secret stored", { timeout: 10000 });
  await shot(page, "wizard-integrations.png");
  console.log("[smoke] integrations page shows Linear with stored secret");

  // 8. Disconnect Linear → expect it gone.
  await Promise.all([
    page.waitForURL(/\/integrations/, { timeout: 10000 }),
    page.getByRole("button", { name: /Disconnect/i }).first().click(),
  ]);
  await page.waitForLoadState("load");
  // Only the "Connect →" affordance for Linear should remain in "Available".
  const stillThere = await page.locator("text=secret stored").count();
  if (stillThere !== 0) {
    throw new Error(`Linear still listed after Disconnect (${stillThere} matches)`);
  }
  await shot(page, "wizard-integrations-after-delete.png");
  console.log("[smoke] disconnect verified");

  await page.close();
  await context.close();
  await browser.close();
  console.log("[smoke] done");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
