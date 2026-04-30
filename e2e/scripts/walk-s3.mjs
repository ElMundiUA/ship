#!/usr/bin/env node
/**
 * S3 walk — automated headless tour of the deployed console for Ship-on-Ship.
 * Uses the saved Auth0 storage state (.auth/user.json) to skip the login flow.
 * Prints a compact summary of what's actually rendered on each surface so we
 * have a paper trail before doing the manual eye-pass.
 */

import { chromium } from "playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STORAGE = path.resolve(__dirname, "..", ".auth", "user.json");
const BASE = "https://app.ship.elmundi.com";

const ROUTES = [
  ["/", "Workspace home"],
  ["/r/ElMundiUA/ship", "Per-repo Ship"],
  ["/inbox", "Inbox"],
  ["/knowledge", "Knowledge"],
  ["/process", "Process"],
  ["/settings", "Settings"],
  ["/integrations", "Integrations"],
  ["/members", "Members"],
];

async function main() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ storageState: STORAGE });
  const page = await ctx.newPage();

  for (const [route, label] of ROUTES) {
    console.log(`\n=== ${label}  (${route}) ===`);
    try {
      const res = await page.goto(BASE + route, {
        waitUntil: "domcontentloaded",
        timeout: 30000,
      });
      // give client-side hydration a moment
      await page.waitForTimeout(1500);
      console.log(`status=${res?.status() ?? "?"}  url=${page.url()}`);

      // headings (h1/h2)
      const headings = await page.evaluate(() =>
        Array.from(document.querySelectorAll("h1, h2"))
          .slice(0, 6)
          .map((h) => `[${h.tagName}] ${(h.textContent || "").trim().slice(0, 100)}`),
      );
      headings.forEach((h) => console.log("  " + h));

      // banners / warnings (any element with role=alert, or text including "warn"/"error")
      const banners = await page.evaluate(() => {
        const out = [];
        document.querySelectorAll('[role="alert"]').forEach((n) => {
          const t = (n.textContent || "").trim();
          if (t) out.push("alert: " + t.slice(0, 200));
        });
        document.querySelectorAll('[class*="border-coral"],[class*="border-sun"],[class*="text-coral"],[class*="text-sun"]').forEach((n) => {
          const t = (n.textContent || "").trim();
          if (t && t.length < 200) out.push("warn-style: " + t);
        });
        return [...new Set(out)].slice(0, 5);
      });
      banners.forEach((b) => console.log("  " + b));

      // CTAs (buttons + primary links)
      const ctas = await page.evaluate(() =>
        Array.from(document.querySelectorAll("a[href], button"))
          .map((el) => (el.textContent || "").trim())
          .filter((t) => t && t.length < 60)
          .slice(0, 12),
      );
      console.log("  CTAs:", ctas.slice(0, 8).join(" · "));

      // bare-snapshot of first 600 chars of visible text
      const text = await page.evaluate(() =>
        (document.body.innerText || "").replace(/\s+/g, " ").slice(0, 800),
      );
      console.log("  text:", text);
    } catch (err) {
      console.log("  ERROR:", err.message?.slice(0, 200));
    }
  }

  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
