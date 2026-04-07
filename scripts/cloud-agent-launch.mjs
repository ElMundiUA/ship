#!/usr/bin/env node
/**
 * Launch Cursor Cloud Agent with role-specific prompt + skills context.
 * Usage: node scripts/cloud-agent-launch.mjs --role=... --issue=TICKET-1
 * Daily audits (no Linear anchor ticket): --role=tech-architect|qa-architect|security-officer --issue=NONE
 * Optional: --report-file=/path/to/self-heal-report.json (appended to prompt; workflow-self-heal)
 *
 * Env: CURSOR_API_KEY (required), REPO_ROOT (optional, default ../../ from this script)
 */
import { readFileSync, existsSync, readdirSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import {
  resolveTechDebtProjectId,
  resolveSecurityProjectId,
  DEFAULT_TECH_DEBT_PROJECT_NAME,
  DEFAULT_SECURITY_PROJECT_NAME,
} from "./lib/audit-linear-projects.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const AGENT_DIR = resolve(__dirname, "..");
const DEFAULT_REPO_ROOT = resolve(AGENT_DIR, "../..");

function parseArgs() {
  const out = { role: "", issue: "", reportFile: "" };
  for (const a of process.argv.slice(2)) {
    if (a.startsWith("--role=")) out.role = a.slice(7);
    if (a.startsWith("--issue=")) out.issue = a.slice(8);
    if (a.startsWith("--report-file=")) out.reportFile = a.slice(14);
  }
  return out;
}

const SDLC_BRANCH_ROLES = new Set(["intake", "clarification", "ba"]);
/** Scheduled analysis: create Linear issues in audit projects; optional issue=NONE. */
const ANALYSIS_ROLES = new Set(["tech-architect", "qa-architect", "security-officer"]);

function collectSkills(repoRoot) {
  const base = join(repoRoot, ".cursor", "skills");
  if (!existsSync(base)) {
    return "(Skills folder not in checkout — add `.cursor/skills` to sparse-checkout.)";
  }
  const chunks = [];
  for (const name of readdirSync(base)) {
    const skillMd = join(base, name, "SKILL.md");
    if (!existsSync(skillMd)) continue;
    try {
      const text = readFileSync(skillMd, "utf8");
      const max = 4000;
      chunks.push(
        `### ${name}\n${text.slice(0, max)}${text.length > max ? "\n...(truncated)" : ""}`
      );
    } catch {
      /* skip */
    }
  }
  return chunks.length ? chunks.join("\n\n---\n\n") : "(No SKILL.md files found.)";
}

function getIssueJson(issueArg) {
  try {
    const buf = execFileSync(
      process.execPath,
      ["dist/cli.js", "get", issueArg, "--json"],
      { cwd: AGENT_DIR, encoding: "utf8", env: process.env, maxBuffer: 10 * 1024 * 1024 }
    );
    return JSON.parse(buf);
  } catch {
    return { title: "", description: "" };
  }
}

function gitRemoteHttps(repoRoot) {
  try {
    const url = execFileSync("git", ["remote", "get-url", "origin"], {
      cwd: repoRoot,
      encoding: "utf8",
    }).trim();
    return url
      .replace(/^git@github.com:/, "https://github.com/")
      .replace(/\.git$/, "");
  } catch {
    return "https://github.com/";
  }
}

async function main() {
  let { role, issue, reportFile } = parseArgs();
  if (ANALYSIS_ROLES.has(role) && !issue) {
    issue = "NONE";
  }
  if (!role || !issue) {
    console.error(
      "Usage: cloud-agent-launch.mjs --role=intake|clarification|ba|developer|workflow-self-heal|tech-architect|qa-architect|security-officer --issue=TICKET-XX|NONE [--report-file=path]"
    );
    process.exit(1);
  }
  const apiKey = process.env.CURSOR_API_KEY;
  if (!apiKey) {
    console.error("CURSOR_API_KEY required");
    process.exit(1);
  }

  const repoRoot = process.env.REPO_ROOT || DEFAULT_REPO_ROOT;
  const roleFile = join(AGENT_DIR, "cloud-prompts", `${role}.md`);
  const baseFile = join(AGENT_DIR, "cloud-prompts", "_base.md");
  if (!existsSync(roleFile)) {
    console.error(`Unknown role or missing prompt: ${roleFile}`);
    process.exit(1);
  }

  const baseTpl = existsSync(baseFile) ? readFileSync(baseFile, "utf8") : "";
  const roleTpl = readFileSync(roleFile, "utf8");
  const skills = collectSkills(repoRoot);

  const getEnv = (k) => process.env[k];
  let meta;
  if (ANALYSIS_ROLES.has(role) && String(issue).toUpperCase() === "NONE") {
    meta = { title: "", description: "" };
    issue = "NONE";
  } else {
    meta = getIssueJson(issue);
  }

  const linearKey = process.env.LINEAR_API_KEY;
  let techDebtProjectId = "";
  let securityProjectId = "";
  let techDebtProjectName = DEFAULT_TECH_DEBT_PROJECT_NAME;
  let securityProjectName = DEFAULT_SECURITY_PROJECT_NAME;
  if (ANALYSIS_ROLES.has(role)) {
    if (!linearKey) {
      console.error("LINEAR_API_KEY required for analysis roles (project resolution)");
      process.exit(1);
    }
    if (process.env.LINEAR_TECH_DEBT_PROJECT_NAME?.trim()) {
      techDebtProjectName = process.env.LINEAR_TECH_DEBT_PROJECT_NAME.trim();
    }
    if (process.env.LINEAR_SECURITY_PROJECT_NAME?.trim()) {
      securityProjectName = process.env.LINEAR_SECURITY_PROJECT_NAME.trim();
    }
    if (role === "security-officer") {
      securityProjectId = (await resolveSecurityProjectId(linearKey, getEnv)) || "";
      if (!securityProjectId) {
        console.error(
          `Security project not found. Run: node scripts/ensure-audit-linear-projects.mjs\nOr set LINEAR_SECURITY_PROJECT_ID / LINEAR_SECURITY_PROJECT_NAME (default "${DEFAULT_SECURITY_PROJECT_NAME}").`
        );
        process.exit(1);
      }
    } else {
      techDebtProjectId = (await resolveTechDebtProjectId(linearKey, getEnv)) || "";
      if (!techDebtProjectId) {
        console.error(
          `Tech debt project not found. Run: node scripts/ensure-audit-linear-projects.mjs\nOr set LINEAR_TECH_DEBT_PROJECT_ID / LINEAR_TECH_DEBT_PROJECT_NAME (default "${DEFAULT_TECH_DEBT_PROJECT_NAME}").`
        );
        process.exit(1);
      }
    }
  }

  const base = baseTpl
    .replace(/\{\{SKILLS_CONTEXT\}\}/g, skills)
    .replace(/\{\{ROLE\}\}/g, role);

  let prompt = roleTpl
    .replace(/\{\{BASE\}\}/g, base)
    .replace(/\{\{ISSUE\}\}/g, issue)
    .replace(/\{\{TITLE\}\}/g, (meta.title || "").slice(0, 500))
    .replace(/\{\{DESCRIPTION\}\}/g, (meta.description || "").slice(0, 8000))
    .replace(/\{\{TECH_DEBT_PROJECT_ID\}\}/g, techDebtProjectId)
    .replace(/\{\{TECH_DEBT_PROJECT_NAME\}\}/g, techDebtProjectName)
    .replace(/\{\{SECURITY_PROJECT_ID\}\}/g, securityProjectId)
    .replace(/\{\{SECURITY_PROJECT_NAME\}\}/g, securityProjectName)
    .replace(/\{\{LINEAR_TEAM_KEY\}\}/g, (process.env.LINEAR_TEAM_KEY || "YOUR_TEAM").trim());

  if (reportFile) {
    const abs = resolve(reportFile);
    if (existsSync(abs)) {
      const raw = readFileSync(abs, "utf8").slice(0, 32000);
      const label =
        role === "security-officer"
          ? "Snyk / security report (JSON)"
          : "Attached report (JSON)";
      prompt += `\n\n---\n\n## ${label}\n\n\`\`\`json\n${raw}\n\`\`\`\n`;
    }
  }

  const repoUrl = gitRemoteHttps(repoRoot);
  // Developer: keep autoCreatePr false — the prompt tells the agent to open one PR with
  // Closes TICKET-XX; Cursor auto-PR + manual PR caused duplicate PRs for the same issue.
  const autoCreatePr = role === "workflow-self-heal";
  const branchName =
    role === "developer"
      ? `fix/${issue}-auto`
      : role === "workflow-self-heal"
        ? `cursor/workflow-self-heal-${Date.now().toString(36)}`
        : ANALYSIS_ROLES.has(role)
          ? `cursor/daily-audit-${role}-${Date.now().toString(36)}`
          : SDLC_BRANCH_ROLES.has(role)
            ? `cursor/${issue}-${role}-sd`
            : `cursor/${issue}-${role}-${Date.now().toString(36)}`;

  const body = JSON.stringify({
    prompt: { text: prompt },
    source: { repository: repoUrl, ref: "main" },
    target: {
      branchName,
      autoCreatePr,
      openAsCursorGithubApp: false,
    },
  });

  const res = await fetch("https://api.cursor.com/v0/agents", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Basic " + Buffer.from(`${apiKey}:`).toString("base64"),
    },
    body,
  });

  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    console.error("Non-JSON response:", text.slice(0, 500));
    process.exit(1);
  }

  if (!res.ok) {
    console.error("Launch failed:", data);
    process.exit(1);
  }

  const id = data.id || data.agentId;
  console.log(JSON.stringify({ ok: true, agentId: id, branch: branchName, role, issue }));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
