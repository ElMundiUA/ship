#!/usr/bin/env node
/**
 * Trigger a wizard re-seed for ElMundiUA/ship via the deployed console
 * by reusing the saved Auth0 session. Submits the dashboard "Open
 * wizard" form and watches the resulting top-level redirect.
 */

import { chromium } from "playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STORAGE = path.resolve(__dirname, "..", ".auth", "user.json");
const BASE = "https://app.ship.elmundi.com";
const WORKSPACE_ID = "d591af28-225e-477e-8448-7a4b9b06fbfc";
const REPO_ID = "0f01d965-d4d7-46c9-bcdd-930b9efdd3b6"; // ElMundiUA/ship

async function main() {
  // Set HEADED=1 to watch the navigation happen.
  const headed = process.env.HEADED === "1";
  const browser = await chromium.launch({
    headless: !headed,
    slowMo: headed ? 100 : 0,
  });
  const ctx = await browser.newContext({ storageState: STORAGE });
  const page = await ctx.newPage();

  await page.goto(BASE + "/", { waitUntil: "domcontentloaded", timeout: 30000 });

  // Submit the dashboard form. Race two outcomes: success → cross-origin
  // nav to github.com (we just need the URL); error → same-origin nav back
  // to "/?reason=…".
  const navPromise = page
    .waitForURL(
      (u) => u.toString().includes("github.com") || u.toString().includes("reason="),
      { timeout: 30000, waitUntil: "commit" },
    )
    .catch((e) => ({ error: e.message }));

  await page.evaluate(
    ({ ws, repo }) => {
      const f = document.createElement("form");
      f.method = "POST";
      f.action = "/api/dashboard/install-bundle";
      f.enctype = "application/x-www-form-urlencoded";
      const wsField = document.createElement("input");
      wsField.name = "ws";
      wsField.value = ws;
      f.appendChild(wsField);
      const repoField = document.createElement("input");
      repoField.name = "repo_id";
      repoField.value = repo;
      f.appendChild(repoField);
      document.body.appendChild(f);
      f.submit();
    },
    { ws: WORKSPACE_ID, repo: REPO_ID },
  );

  const result = await navPromise;
  if (result && result.error) {
    console.log("nav-wait error:", result.error);
    console.log("current url:", page.url());
  }
  console.log("final url:", page.url());
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
