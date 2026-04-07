#!/usr/bin/env node
/**
 * qa-browser CLI - browser automation for QA agents (Playwright-based).
 */
import "dotenv/config";
import { program } from "commander";
import { chromium } from "playwright";
import { readFileSync, mkdirSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { loadConfig } from "./config.js";
const config = loadConfig();
const artifactsDir = resolve(process.cwd(), config.qaBrowser.artifactsDir);
const jsonOutput = process.argv.includes("--json");
let browser = null;
let page = null;
function ensureArtifactsDir() {
    if (!existsSync(artifactsDir)) {
        mkdirSync(artifactsDir, { recursive: true });
    }
}
function out(obj) {
    if (jsonOutput) {
        console.log(JSON.stringify(obj, null, 2));
    }
    else {
        console.log(obj);
    }
}
async function getPage() {
    if (!page) {
        browser = await chromium.launch({ headless: config.qaBrowser.headless });
        const ctx = await browser.newContext();
        page = await ctx.newPage();
    }
    return page;
}
program
    .name("qa-browser")
    .description("Browser automation CLI for QA agents")
    .option("--json", "Output as JSON")
    .option("--headed", "Run browser in headed mode")
    .option("-o, --output <dir>", "Artifacts directory", config.qaBrowser.artifactsDir);
program
    .command("open")
    .description("Open a URL")
    .argument("<url>", "URL to open")
    .action(async (url) => {
    const p = await getPage();
    await p.goto(url, { waitUntil: "domcontentloaded" });
    out({ ok: true, url });
});
program
    .command("click")
    .description("Click an element")
    .requiredOption("-s, --selector <selector>", "CSS selector or data-testid")
    .action(async (opts) => {
    const sel = opts.selector.startsWith("[") ? opts.selector : `[data-testid="${opts.selector}"]`;
    const p = await getPage();
    await p.click(sel, { timeout: 10000 });
    out({ ok: true, selector: opts.selector });
});
program
    .command("type")
    .description("Type text into an element")
    .requiredOption("-s, --selector <selector>", "CSS selector")
    .requiredOption("-t, --text <text>", "Text to type")
    .action(async (opts) => {
    const sel = opts.selector.startsWith("[") ? opts.selector : `[data-testid="${opts.selector}"]`;
    const p = await getPage();
    await p.fill(sel, opts.text);
    out({ ok: true });
});
program
    .command("assert-text")
    .description("Assert element contains text")
    .requiredOption("-s, --selector <selector>", "CSS selector")
    .requiredOption("--contains <text>", "Expected text (substring)")
    .action(async (opts) => {
    const sel = opts.selector.startsWith("[") ? opts.selector : `[data-testid="${opts.selector}"]`;
    const p = await getPage();
    const el = await p.locator(sel).first();
    const text = await el.textContent();
    const contains = (text ?? "").includes(opts.contains);
    if (!contains) {
        out({ ok: false, expected: opts.contains, actual: text });
        if (browser)
            await browser.close();
        process.exit(1);
    }
    out({ ok: true, contains: opts.contains });
});
program
    .command("screenshot")
    .description("Take a screenshot")
    .option("-o, --output <path>", "Output path", "screenshot.png")
    .action(async (opts) => {
    ensureArtifactsDir();
    const outputPath = resolve(artifactsDir, opts.output);
    const p = await getPage();
    await p.screenshot({ path: outputPath });
    out({ ok: true, path: outputPath });
});
program
    .command("run")
    .description("Run a spec file (YAML/JSON scenario)")
    .argument("<spec>", "Path to spec file")
    .action(async (specPath) => {
    const fullPath = resolve(process.cwd(), specPath);
    const content = readFileSync(fullPath, "utf-8");
    let steps;
    try {
        if (specPath.endsWith(".yaml") || specPath.endsWith(".yml")) {
            const yaml = await import("yaml");
            steps = yaml.parse(content);
        }
        else {
            steps = JSON.parse(content);
        }
    }
    catch (e) {
        console.error("Failed to parse spec:", e);
        process.exit(1);
    }
    const p = await getPage();
    const results = [];
    for (let i = 0; i < steps.length; i++) {
        const step = steps[i];
        try {
            if (step.action === "open" && step.url) {
                await p.goto(step.url, { waitUntil: "domcontentloaded" });
            }
            else if (step.action === "click" && step.selector) {
                const sel = step.selector.startsWith("[") ? step.selector : `[data-testid="${step.selector}"]`;
                await p.click(sel, { timeout: 10000 });
            }
            else if (step.action === "type" && step.selector && step.text) {
                const sel = step.selector.startsWith("[") ? step.selector : `[data-testid="${step.selector}"]`;
                await p.fill(sel, step.text);
            }
            results.push({ step: i + 1, action: step.action, ok: true });
        }
        catch (err) {
            results.push({
                step: i + 1,
                action: step.action,
                ok: false,
                error: err instanceof Error ? err.message : String(err),
            });
            out({ ok: false, results });
            if (browser)
                await browser.close();
            process.exit(1);
        }
    }
    out({ ok: true, results });
});
program
    .command("trace-start")
    .description("Start tracing")
    .action(async () => {
    const p = await getPage();
    await p.context().tracing.start({ screenshots: true, snapshots: true });
    out({ ok: true, message: "Tracing started" });
});
program
    .command("trace-stop")
    .description("Stop tracing and save")
    .option("-o, --output <path>", "Output path", "trace.zip")
    .action(async (opts) => {
    ensureArtifactsDir();
    const outputPath = resolve(artifactsDir, opts.output);
    const p = await getPage();
    await p.context().tracing.stop({ path: outputPath });
    out({ ok: true, path: outputPath });
});
program
    .command("close")
    .description("Close browser")
    .action(async () => {
    if (browser) {
        await browser.close();
        browser = null;
        page = null;
    }
    out({ ok: true });
});
program.parseAsync().finally(async () => {
    if (browser) {
        await browser.close();
    }
});
//# sourceMappingURL=qa-browser-cli.js.map