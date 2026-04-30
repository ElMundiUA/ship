#!/usr/bin/env node
/**
 * Capture the full /inbox 500 response and any debug crumbs in the body.
 */

import { chromium } from "playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STORAGE = path.resolve(__dirname, "..", ".auth", "user.json");
const BASE = "https://app.ship.elmundi.com";

async function main() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ storageState: STORAGE });
  const page = await ctx.newPage();

  // log every console message, every request, every response
  page.on("console", (msg) => {
    console.log(`[console.${msg.type()}] ${msg.text()}`);
  });
  page.on("pageerror", (err) => {
    console.log(`[pageerror] ${err.message}`);
  });
  page.on("requestfailed", (req) => {
    console.log(`[reqfailed] ${req.url()} → ${req.failure()?.errorText}`);
  });
  page.on("response", async (res) => {
    if (res.url().includes("/v1/") || res.status() >= 400) {
      console.log(`[resp] ${res.status()} ${res.url()}`);
    }
  });

  const url = BASE + "/inbox";
  console.log(`>> GET ${url}`);
  const res = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
  console.log(`<< status=${res?.status()}`);
  await page.waitForTimeout(3000);

  // dump the WHOLE html body
  const html = await page.content();
  console.log("\n========== FULL HTML (first 30000 chars) ==========");
  console.log(html.slice(0, 30000));
  console.log("========== END (total length: " + html.length + ") ==========\n");

  // grep for any digest / error markers
  const matches = html.match(/digest[^"<]*|error[^"<]*"[^"]{0,200}/gi);
  if (matches) {
    console.log("error-like substrings:");
    matches.slice(0, 20).forEach((m) => console.log("  •", m.slice(0, 200)));
  }

  // pull window.__NEXT_DATA__ if present
  const nextData = await page.evaluate(() => {
    const el = document.getElementById("__NEXT_DATA__");
    return el?.textContent ?? null;
  });
  if (nextData) {
    console.log("\n__NEXT_DATA__:", nextData.slice(0, 2000));
  }

  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
