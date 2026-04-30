#!/usr/bin/env node
/**
 * Bind GitHub Issues as the tracker for ElMundiUA/ship in the active workspace.
 * Reuses the saved Auth0 session.
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
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ storageState: STORAGE });
  const page = await ctx.newPage();

  // Land on console so iron-session cookies attach.
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded", timeout: 30000 });

  // PUT /api/workspaces/{ws}/repos/{repo}/tracker is rewritten to
  // backend's /v1/.../tracker. The console's session cookie is exchanged
  // server-side into the backend bearer.
  const url = `${BASE}/api/workspaces/${WORKSPACE_ID}/repos/${REPO_ID}/tracker`;
  console.log(">> PUT", url);
  const res = await page.evaluate(async (u) => {
    const r = await fetch(u, {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "github", config: {} }),
    });
    return { status: r.status, body: await r.text() };
  }, url);
  console.log("status=", res.status);
  console.log("body:", res.body.slice(0, 500));

  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
