import { chromium } from "playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const storageStatePath = path.join(__dirname, "..", ".auth", "user.json");

const url = process.argv[2] || "https://app.ship.elmundi.com/";
const out = process.argv[3] || "/tmp/console-auth.png";
const w = parseInt(process.argv[4] ?? "1600", 10);
const h = parseInt(process.argv[5] ?? "1100", 10);

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: w, height: h },
  storageState: storageStatePath,
});
const page = await ctx.newPage();

const errors = [];
page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
});

const resp = await page.goto(url, { waitUntil: "networkidle", timeout: 30000 });
await page.waitForTimeout(1200);
await page.screenshot({ path: out, fullPage: true });

console.log(`URL: ${url}`);
console.log(`STATUS: ${resp?.status()}`);
console.log(`FINAL: ${page.url()}`);
console.log(`TITLE: ${await page.title()}`);
console.log(`ERRORS:`);
for (const e of errors) console.log(`  ${e}`);
console.log(`SCREENSHOT: ${out}`);
await browser.close();
