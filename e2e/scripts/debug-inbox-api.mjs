#!/usr/bin/env node
/**
 * Hit the deployed backend's /v1/.../inbox endpoint with the saved
 * Auth0 session and dump every byte of the response so we can see
 * exactly what server data the page is feeding into render.
 */

import { chromium } from "playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STORAGE = path.resolve(__dirname, "..", ".auth", "user.json");
const BASE = "https://app.ship.elmundi.com";
const WS = "d591af28-225e-477e-8448-7a4b9b06fbfc";

async function main() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ storageState: STORAGE });
  const page = await ctx.newPage();

  // Need to land on the app domain so cookies are scoped + CORS doesn't bite.
  await page.goto(BASE + "/auth/me-noop", { waitUntil: "domcontentloaded" }).catch(() => {});

  // Hit the proxy that forwards to backend (rewrites /api/* → backend/v1/*)
  // The proxy uses the same Auth0 session
  const url = `${BASE}/api/workspaces/${WS}/inbox?status=new&status=snoozed&limit=25`;
  console.log(`>> GET ${url}`);
  const res = await page.evaluate(async (u) => {
    const r = await fetch(u, { credentials: "include" });
    return { status: r.status, body: await r.text() };
  }, url);
  console.log(`<< status=${res.status}`);
  console.log(`<< body:`);
  try {
    const json = JSON.parse(res.body);
    console.log(JSON.stringify(json, null, 2));
  } catch {
    console.log(res.body);
  }
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
