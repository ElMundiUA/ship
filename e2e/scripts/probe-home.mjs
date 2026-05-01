import { chromium } from "playwright";

const url = process.argv[2] || "https://ship.elmundi.com/";
const out = process.argv[3] || "/tmp/home-screenshot.png";
const w = parseInt(process.argv[4] ?? "1440", 10);
const h = parseInt(process.argv[5] ?? "900", 10);

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: w, height: h } });
const page = await ctx.newPage();

const errors = [];
page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
page.on("console", (msg) => {
  if (msg.type() === "error" || msg.type() === "warning") {
    errors.push(`${msg.type()}: ${msg.text()}`);
  }
});
page.on("requestfailed", (req) => errors.push(`requestfailed: ${req.url()} ${req.failure()?.errorText}`));

const resp = await page.goto(url, { waitUntil: "networkidle", timeout: 30000 });
await page.waitForTimeout(800);
await page.screenshot({ path: out, fullPage: true });

console.log(`URL: ${url}`);
console.log(`VIEWPORT: ${w}×${h}`);
console.log(`STATUS: ${resp?.status()}`);
console.log(`TITLE: ${await page.title()}`);
console.log(`ERRORS:`);
for (const e of errors) console.log(`  ${e}`);
console.log(`SCREENSHOT: ${out}`);
await browser.close();
