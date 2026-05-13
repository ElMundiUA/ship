#!/usr/bin/env node
/**
 * Records a guided tour of the operator console (mock data) as a webm video.
 *
 * Usage:
 *   node scripts/console-tour.mjs           # tours http://localhost:3001
 *   BASE_URL=http://localhost:3001 node scripts/console-tour.mjs
 *
 * Output:
 *   output/playwright/console-tour.webm
 */

import { chromium } from "playwright";
import { mkdir, readdir, rename, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const OUT_DIR = path.join(REPO_ROOT, "output", "playwright");
const FINAL_PATH = path.join(OUT_DIR, "console-tour.webm");

const BASE_URL = process.env.BASE_URL ?? "http://localhost:3001";
const VIEWPORT = { width: 1440, height: 900 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** A page in the tour. `scroll: true` does a slow scroll to the bottom. */
const STOPS = [
  { path: "/login", hold: 1800 },
  { path: "/onboarding", hold: 2200 },
  { path: "/", hold: 2200, scroll: true, label: "Operating dashboard" },
  { path: "/catalog", hold: 1800, scroll: true, label: "Catalog" },
  { path: "/catalog/onboard-adopt", hold: 2400, scroll: true, label: "Artifact detail" },
  { path: "/catalog/pull-requests", hold: 2000, scroll: true, label: "Catalog PRs" },
  { path: "/knowledge", hold: 1800, scroll: true, label: "Knowledge buckets" },
  { path: "/knowledge/kb_devops", hold: 2400, scroll: true, label: "Bucket detail" },
  { path: "/workflows", hold: 1800, scroll: true, label: "Workflow runs" },
  { path: "/daily", hold: 2200, scroll: true, label: "Daily & retro" },
  { path: "/effectiveness", hold: 2400, scroll: true, label: "Effectiveness" },
  { path: "/telemetry", hold: 1800, scroll: true, label: "Telemetry" },
  { path: "/members", hold: 1600, scroll: true, label: "Members" },
  { path: "/integrations", hold: 1600, scroll: true, label: "Integrations" },
  { path: "/settings", hold: 1600, scroll: true, label: "Settings" },
  { path: "/preview/empty", hold: 2200, scroll: true, label: "Empty states" },
];

async function slowScroll(page, durationMs = 1500) {
  await page.evaluate(async (durationMs) => {
    const max = Math.max(
      document.body.scrollHeight - window.innerHeight,
      0,
    );
    if (max <= 0) return;
    const start = performance.now();
    return new Promise((resolve) => {
      function step(now) {
        const t = Math.min(1, (now - start) / durationMs);
        // ease-in-out
        const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
        window.scrollTo(0, max * eased);
        if (t < 1) requestAnimationFrame(step);
        else resolve();
      }
      requestAnimationFrame(step);
    });
  }, durationMs);
}

async function ensureDir(dir) {
  if (!existsSync(dir)) await mkdir(dir, { recursive: true });
}

async function main() {
  await ensureDir(OUT_DIR);
  if (existsSync(FINAL_PATH)) await rm(FINAL_PATH);

  console.log(`[tour] base url: ${BASE_URL}`);
  console.log(`[tour] output:   ${FINAL_PATH}`);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 1,
    recordVideo: { dir: OUT_DIR, size: VIEWPORT },
  });
  const page = await context.newPage();

  for (const stop of STOPS) {
    const url = `${BASE_URL}${stop.path}`;
    const tag = stop.label ? `${stop.label} (${stop.path})` : stop.path;
    process.stdout.write(`[tour] -> ${tag}\n`);

    try {
      await page.goto(url, { waitUntil: "networkidle", timeout: 20000 });
    } catch (err) {
      console.warn(`[tour] !! navigation slow for ${url}: ${err.message}`);
    }

    await sleep(800);
    if (stop.scroll) {
      await slowScroll(page, 1600);
      await sleep(400);
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
      await sleep(600);
    }
    await sleep(stop.hold);
  }

  await page.close();
  await context.close();
  await browser.close();

  // Playwright writes the video with an opaque name; rename it to console-tour.webm.
  const files = (await readdir(OUT_DIR)).filter((f) => f.endsWith(".webm"));
  if (files.length === 0) {
    throw new Error("Playwright did not produce a video file.");
  }
  // Pick the most recent.
  const stats = await Promise.all(
    files.map(async (f) => {
      const p = path.join(OUT_DIR, f);
      const { default: fs } = await import("node:fs");
      return { p, mtime: fs.statSync(p).mtimeMs };
    }),
  );
  stats.sort((a, b) => b.mtime - a.mtime);
  await rename(stats[0].p, FINAL_PATH);

  console.log(`[tour] done -> ${path.relative(REPO_ROOT, FINAL_PATH)}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
