#!/usr/bin/env node
/**
 * Manual single-shot launcher for a Cursor Cloud agent against a Ship
 * pattern + GitHub issue. Mirrors `tools/linear-agent/scripts/cloud-agent-launch.mjs`
 * from the ElMundi sibling repo, adapted to:
 *
 *   - Ship pattern frontmatter (artifacts/patterns/<role>/ARTIFACT.md)
 *   - GitHub Issues as the tracker (instead of Linear)
 *
 * Usage:
 *   CURSOR_API_KEY=… node e2e/scripts/launch-cursor-agent.mjs \
 *     --role role-intake --owner ElMundiUA --repo ship --issue 66
 *
 * Reads the pattern body, substitutes {{ISSUE}}, {{TITLE}}, {{DESCRIPTION}},
 * {{BASE}}, posts the prompt to the Cursor Cloud Agent API. The agent
 * is responsible for talking back to GitHub via `gh` CLI / GH App.
 *
 * This is the manual-iteration path before the server endpoint
 * (E14 T01) takes over.
 */

import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..");
const ARTIFACTS = join(REPO_ROOT, "artifacts", "patterns");

function parseArgs() {
  const out = { role: "", owner: "", repo: "", issue: "", branch: "", autoCreatePr: false };
  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i += 1) {
    const a = args[i];
    if (a === "--role") out.role = args[++i];
    else if (a === "--owner") out.owner = args[++i];
    else if (a === "--repo") out.repo = args[++i];
    else if (a === "--issue") out.issue = String(args[++i]);
    else if (a === "--branch") out.branch = args[++i];
    else if (a === "--auto-create-pr") out.autoCreatePr = true;
  }
  return out;
}

function readPatternBody(role) {
  const p = join(ARTIFACTS, role, "ARTIFACT.md");
  if (!existsSync(p)) throw new Error(`Pattern not found: ${p}`);
  const raw = readFileSync(p, "utf8");
  // Strip frontmatter — body starts after the second "---".
  const idx = raw.indexOf("\n---\n", 4);
  const body = idx >= 0 ? raw.slice(idx + 5) : raw;
  return body.trim();
}

function readBase() {
  const p = join(ARTIFACTS, "common-base", "ARTIFACT.md");
  if (!existsSync(p)) return "";
  const raw = readFileSync(p, "utf8");
  const idx = raw.indexOf("\n---\n", 4);
  return idx >= 0 ? raw.slice(idx + 5).trim() : raw;
}

function ghIssue(owner, repo, n) {
  const buf = execFileSync(
    "gh",
    ["issue", "view", n, "--repo", `${owner}/${repo}`, "--json", "number,title,body,url,labels,state"],
    { encoding: "utf8", maxBuffer: 8 * 1024 * 1024 },
  );
  return JSON.parse(buf);
}

async function main() {
  const opts = parseArgs();
  if (!opts.role || !opts.owner || !opts.repo || !opts.issue) {
    console.error("Usage: --role <pattern-id> --owner <gh-owner> --repo <gh-repo> --issue <number>");
    process.exit(1);
  }
  const apiKey = process.env.CURSOR_API_KEY;
  if (!apiKey) {
    console.error("CURSOR_API_KEY required");
    process.exit(1);
  }

  const issue = ghIssue(opts.owner, opts.repo, opts.issue);
  const baseBody = readBase()
    .replace(/\{\{SKILLS_CONTEXT\}\}/g, "(no skills directory in this repo)")
    .replace(/\{\{ROLE\}\}/g, opts.role)
    .replace(/\{\{ISSUE\}\}/g, `#${issue.number}`);

  const body = readPatternBody(opts.role);
  const issueRef = `#${issue.number}`;
  const prompt = body
    .replace(/\{\{BASE\}\}/g, baseBody)
    .replace(/\{\{ISSUE\}\}/g, issueRef)
    .replace(/\{\{TITLE\}\}/g, (issue.title || "").slice(0, 500))
    .replace(/\{\{DESCRIPTION\}\}/g, (issue.body || "").slice(0, 8000));

  // Override + clarify: this is a GitHub Issues workflow, not Linear.
  const ghPreamble = `
## How to act on GitHub (this repo uses GitHub Issues, not Linear)

The single human-facing channel for this ticket is the GitHub issue ${issueRef} on ${opts.owner}/${opts.repo}.
Use the \`gh\` CLI for everything:

- Read latest state: \`gh issue view ${opts.issue} --repo ${opts.owner}/${opts.repo} --json title,body,labels,state,comments\`
- Comment: \`gh issue comment ${opts.issue} --repo ${opts.owner}/${opts.repo} --body "..."\`
- Label: \`gh issue edit ${opts.issue} --repo ${opts.owner}/${opts.repo} --add-label "ready"\` / \`--remove-label "needs-info"\`
- Close: \`gh issue close ${opts.issue} --repo ${opts.owner}/${opts.repo}\` (only when role is the right one to close)

End every comment with the marker line \`[Ship SDLC:${opts.role}]\` (one comment per run, not multiple).

If a comment with \`[Ship SDLC:${opts.role}]\` already reflects the current state — exit without re-commenting.

`;

  const fullPrompt = ghPreamble + prompt;

  const branchName = opts.branch || `cursor/ship-${opts.role}-issue-${issue.number}-${Date.now().toString(36)}`;
  const repoUrl = `https://github.com/${opts.owner}/${opts.repo}`;

  const reqBody = JSON.stringify({
    prompt: { text: fullPrompt },
    source: { repository: repoUrl, ref: "main" },
    target: {
      branchName,
      autoCreatePr: opts.autoCreatePr,
      openAsCursorGithubApp: false,
    },
  });

  console.log("== prompt preview (first 500 chars) ==");
  console.log(fullPrompt.slice(0, 500));
  console.log("== POST https://api.cursor.com/v0/agents ==");

  const res = await fetch("https://api.cursor.com/v0/agents", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Basic " + Buffer.from(`${apiKey}:`).toString("base64"),
    },
    body: reqBody,
  });
  const text = await res.text();
  if (!res.ok) {
    console.error("Cursor API failed:", res.status, text.slice(0, 1000));
    process.exit(1);
  }
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  console.log("OK:", JSON.stringify(data, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
