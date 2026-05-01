import { chromium } from "playwright";

const url = process.argv[2] || "http://localhost:3000/";
const out = process.argv[3] || "/tmp/hero-figure.png";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
const page = await ctx.newPage();
await page.goto(url, { waitUntil: "networkidle", timeout: 30000 });
await page.waitForTimeout(800);
const fig = page.locator("figure").first();
await fig.scrollIntoViewIfNeeded();
await fig.screenshot({ path: out });
await browser.close();
console.log(`SAVED ${out}`);
