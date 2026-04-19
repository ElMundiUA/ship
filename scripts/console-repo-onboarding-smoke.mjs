#!/usr/bin/env node
/**
 * End-to-end smoke for the repo-driven onboarding wizard.
 *
 * Walks the full new flow:
 *
 *   1. Sign up at /login → land on /onboarding (auto-redirect for empty users)
 *   2. step=repo  — click "Use a demo repo" (backend scaffolds + inspects)
 *   3. step=workspace — confirm suggested name + slug → submit
 *   4. step=workflows — leave the recommended boxes ticked → install
 *   5. step=tracker (was integration) — pick Linear, drop a fake secret
 *   6. step=knowledge — leave all three buckets ticked → seed
 *   7. step=token — mint, verify a token came back
 *   8. step=done → visit /catalog and confirm live banner
 *   9. /integrations → Linear with "secret stored", click "Probe now"
 *  10. /settings → toggle the Global catalog source off and back on
 *
 * Screenshots land under output/playwright/repo-onboarding/ for visual review.
 */

import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const OUT_DIR = path.join(REPO_ROOT, "output", "playwright", "repo-onboarding");

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
  const email = `repo-${stamp}@ship.dev`;
  const password = "ship-tour-pass-1234";
  const slug = `repo-${stamp.toString(36)}`;
  console.log(`[smoke] base=${BASE_URL} email=${email}`);

  // Disable Chromium's built-in autofill heuristics. They mutate `<input>`
  // elements (notably injecting `style="caret-color: transparent ..."`)
  // mid-hydration, which racingly trips React #418 even though the SSR
  // markup is correct. Real users hit the same class of issue via password
  // managers, which is why we also set `suppressHydrationWarning` on the
  // server-rendered inputs themselves.
  const browser = await chromium.launch({
    headless: true,
    args: [
      "--disable-features=AutofillServerCommunication,AutofillEnableAccountWalletStorage,Autofill,PasswordManagerOnboarding",
    ],
  });
  const context = await browser.newContext({ viewport: VIEWPORT });
  const page = await context.newPage();

  // React's prod build raises a "#418 HTML mismatch" any time a tool
  // mutates the DOM in the same tick that React is hydrating. In practice
  // we still see this from headless Chromium's autofill heuristics (the
  // `--disable-features=Autofill,...` browser arg above doesn't kill all
  // of them) and real users hit the equivalent via password-manager
  // extensions. We've already added `suppressHydrationWarning` on every
  // form `<input>` we render server-side, but the warning still surfaces
  // because React reports the mismatch *before* checking the suppress
  // flag. Treat it as known noise here so the smoke stays meaningful.
  page.on("pageerror", (e) => {
    const isHydration418 = /Minified React error #418/.test(e.message);
    const tag = isHydration418 ? "[pageerror:hydration-noise]" : "[pageerror]";
    console.log(tag, page.url(), e.message);
  });
  page.on("response", (r) => {
    if (r.status() >= 400) console.log("[http]", r.status(), r.url());
  });

  // 1. Sign up.
  await page.goto(`${BASE_URL}/login`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("load");
  await page.getByRole("button", { name: "Create an account" }).click();
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="password"]', password);
  await page.fill('input[name="display_name"]', "Repo Wizard");
  await Promise.all([
    page.waitForURL(/\/(onboarding|)/, { timeout: 15000 }),
    page.getByRole("button", { name: /Create account/i }).click(),
  ]);
  await page.waitForURL(/\/onboarding/, { timeout: 10000 });
  console.log("[smoke] auto-redirected to onboarding");
  await shot(page, "01-step-repo.png");

  // 2. Repo step — click "Use a demo repo" so the backend scaffolds + inspects.
  await Promise.all([
    page.waitForURL(/step=workspace/, { timeout: 30000 }),
    page.getByRole("button", { name: /Use a demo repo/i }).click(),
  ]);
  console.log("[smoke] demo repo inspected");
  await shot(page, "02-step-workspace.png");

  // 3. Workspace step — replace the suggested slug with our unique one to
  //    avoid collisions across repeated smoke runs.
  await page.fill('input[name="slug"]', slug);
  await page.fill('input[name="name"]', "Repo Wizard Workspace");
  await Promise.all([
    page.waitForURL(/step=workflows/, { timeout: 15000 }),
    page.getByRole("button", { name: /Create workspace/i }).click(),
  ]);
  console.log("[smoke] workspace created, on workflows step");
  await shot(page, "03-step-workflows.png");

  // 4. Workflows step — leave the recommended boxes ticked, install.
  await Promise.all([
    page.waitForURL(/step=tracker/, { timeout: 30000 }),
    page.getByRole("button", { name: /Install & commit/i }).click(),
  ]);
  console.log("[smoke] workflows installed + committed");
  await shot(page, "04-step-tracker.png");

  // The tracker step should report the install summary.
  await page.waitForSelector("text=/Installed [0-9]+ workflow/i", { timeout: 5000 });

  // 5. Tracker step — pick Linear (default), drop a fake secret.
  await page.fill('input[name="config_team_id"]', "ENG");
  await page.fill('input[name="secret"]', "lin_api_repo_smoke_secret");
  await Promise.all([
    page.waitForURL(/step=knowledge/, { timeout: 15000 }),
    page.getByRole("button", { name: /Save & continue/i }).click(),
  ]);
  console.log("[smoke] tracker secret saved, on knowledge step");
  await shot(page, "05-step-knowledge.png");

  // 6. Knowledge step — seed all three buckets.
  await Promise.all([
    page.waitForURL(/step=token/, { timeout: 30000 }),
    page.getByRole("button", { name: /Seed & commit/i }).click(),
  ]);
  console.log("[smoke] knowledge seeded + committed");
  await shot(page, "06-step-token.png");

  // 7. Token step — mint, confirm it shows.
  await page.getByRole("button", { name: /Generate token/i }).click();
  await page.waitForSelector("text=Personal Access Token", { timeout: 15000 });
  const tokenValue = await page.inputValue("input[readonly]");
  if (!tokenValue || tokenValue.length < 20) {
    throw new Error(`token input looked empty: '${tokenValue}'`);
  }
  console.log(`[smoke] minted token (prefix: ${tokenValue.slice(0, 12)}…)`);
  await shot(page, "07-step-token-after.png");

  // 8. Finish + visit catalog.
  await page.getByRole("link", { name: /I saved it · finish/i }).click();
  await page.waitForURL(/step=done/, { timeout: 10000 });
  await shot(page, "08-step-done.png");
  await page.getByRole("link", { name: /Open catalog/i }).click();
  await page.waitForURL(/\/catalog/, { timeout: 10000 });
  await page.waitForSelector("text=Reading from", { timeout: 10000 });
  await shot(page, "09-catalog.png");
  console.log("[smoke] catalog showed live data");

  // 9. Integrations page should show Linear with "secret stored".
  await page.goto(`${BASE_URL}/integrations`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("load");
  await page.waitForSelector("text=Linear", { timeout: 10000 });
  await page.waitForSelector("text=secret stored", { timeout: 10000 });
  await shot(page, "10-integrations.png");
  console.log("[smoke] integrations page shows Linear with stored secret");

  // 9b. Click "Probe now" — the page should reload and show a "Probed ..."
  //     line. The fake key will resolve to status=error which is exactly
  //     what we want here: it proves the probe flowed end-to-end.
  await page.locator("form[action='/api/integrations/probe']").first().locator("button").click();
  await page.waitForLoadState("load");
  await page.waitForSelector("text=Probed", { timeout: 10000 });
  await shot(page, "11-integrations-after-probe.png");
  console.log("[smoke] probe-now updated last_health_at");

  // 10. Settings page — flip the Global catalog source toggle off then on so
  //     the round-trip through PATCH /v1/workspaces is exercised against a
  //     real session. The page is a server component, so each click is a
  //     full POST + 303 + reload.
  await page.goto(`${BASE_URL}/settings`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("load");
  await page.waitForSelector("text=Catalog sources", { timeout: 10000 });
  await shot(page, "12-settings-before.png");

  const globalToggle = page
    .locator("form[action='/api/settings/catalog-sources']")
    .filter({ has: page.locator("input[name='key'][value='global']") });
  await globalToggle.locator("button").click();
  await page.waitForLoadState("load");
  await shot(page, "13-settings-toggled-off.png");

  // Flip back on so re-runs of this smoke are idempotent.
  await page
    .locator("form[action='/api/settings/catalog-sources']")
    .filter({ has: page.locator("input[name='key'][value='global']") })
    .locator("button")
    .click();
  await page.waitForLoadState("load");
  await shot(page, "14-settings-toggled-on.png");
  console.log("[smoke] catalog-sources toggle round-tripped");

  // 11. Add an extra artifact-repo row, then remove it. This exercises the
  //     newly-wired "+ Add repo" form and per-row "Remove" button. We use a
  //     unique file:// URL so re-runs of this smoke don't collide with each
  //     other or with the project repo registered during onboarding.
  const extraRepoUrl = `file:///tmp/ship-smoke-extra-${stamp.toString(36)}`;
  await page.locator("details > summary", { hasText: "+ Add repo" }).click();
  const addForm = page.locator(
    "form[action='/api/settings/artifact-repos/create']",
  );
  await addForm.locator("select[name='kind']").selectOption("workspace");
  await addForm.locator("input[name='url']").fill(extraRepoUrl);
  await Promise.all([
    page.waitForLoadState("load"),
    addForm.locator("button[type='submit']").click(),
  ]);
  await page.waitForSelector(`text=${extraRepoUrl}`, { timeout: 10000 });
  await shot(page, "15-settings-repo-added.png");
  console.log("[smoke] extra artifact repo registered");

  // The new row owns its own delete form. We scope the click to the <tr>
  // that contains the URL we just added so we don't accidentally remove
  // the project repo created during onboarding.
  const extraRow = page
    .locator("tr")
    .filter({ has: page.locator(`code:has-text("${extraRepoUrl}")`) });
  await Promise.all([
    page.waitForLoadState("load"),
    extraRow.locator("button", { hasText: /Remove/i }).click(),
  ]);
  await page.waitForFunction(
    (url) => !document.body.innerText.includes(url),
    extraRepoUrl,
    { timeout: 10000 },
  );
  await shot(page, "16-settings-repo-removed.png");
  console.log("[smoke] extra artifact repo removed");

  await page.close();
  await context.close();
  await browser.close();
  console.log("[smoke] done");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
