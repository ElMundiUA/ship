#!/usr/bin/env node
/**
 * Create Linear projects for daily audit roles if missing.
 * Default project names: "Tech debt", "Security" (override via env — see .env.example).
 *
 * Usage: cd tools/linear-agent && node scripts/ensure-audit-linear-projects.mjs [--dry-run]
 * Requires LINEAR_API_KEY (+ LINEAR_TEAM_KEY) in .env
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { linearGraphql, resolveTeam } from "./lib/linear-fetch.mjs";
import {
  DEFAULT_SECURITY_PROJECT_NAME,
  DEFAULT_TECH_DEBT_PROJECT_NAME,
} from "./lib/audit-linear-projects.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ENV_PATH = resolve(__dirname, "../.env");
const DRY = process.argv.includes("--dry-run");

function loadDotenv() {
  const env = {};
  if (existsSync(ENV_PATH)) {
    for (const line of readFileSync(ENV_PATH, "utf8").split("\n")) {
      const m = line.match(/^([^#=]+)=(.*)$/);
      if (m) env[m[1].trim()] = m[2].trim().replace(/^["']|["']$/g, "");
    }
  }
  return env;
}

async function listProjects(apiKey) {
  const data = await linearGraphql(
    apiKey,
    `query {
      projects(first: 200) {
        nodes { id name }
      }
    }`
  );
  return data.projects?.nodes ?? [];
}

async function createProject(apiKey, teamId, name, description) {
  const data = await linearGraphql(
    apiKey,
    `mutation($input: ProjectCreateInput!) {
      projectCreate(input: $input) {
        success
        project { id name url }
      }
    }`,
    {
      input: {
        name,
        description,
        teamIds: [teamId],
      },
    }
  );
  return data.projectCreate?.project ?? null;
}

async function main() {
  const dot = loadDotenv();
  const apiKey = process.env.LINEAR_API_KEY || dot.LINEAR_API_KEY;
  if (!apiKey) {
    console.error("LINEAR_API_KEY required");
    process.exit(1);
  }
  const teamKey = process.env.LINEAR_TEAM_KEY || dot.LINEAR_TEAM_KEY || "ELM";
  const team = await resolveTeam(linearGraphql, apiKey, teamKey);
  if (!team) {
    console.error("Team not found:", teamKey);
    process.exit(1);
  }

  const existing = await listProjects(apiKey);
  const byLower = new Map(existing.map((p) => [p.name.toLowerCase(), p]));

  const want = [
    {
      name: DEFAULT_TECH_DEBT_PROJECT_NAME,
      description:
        "Tech debt & architecture / QA test-gap findings from scheduled GitHub audits. Backlog only — not SDLC pre-release picks.",
    },
    {
      name: DEFAULT_SECURITY_PROJECT_NAME,
      description:
        "Dependency and security findings (e.g. Snyk). Prioritize by severity. Not SDLC pre-release picks.",
    },
  ];

  for (const w of want) {
    const hit = byLower.get(w.name.toLowerCase());
    if (hit) {
      console.log("exists:", hit.name, hit.id);
      continue;
    }
    if (DRY) {
      console.log("would create:", w.name);
      continue;
    }
    const proj = await createProject(apiKey, team.id, w.name, w.description);
    if (proj) {
      console.log("created:", proj.name, proj.id, proj.url ?? "");
    } else {
      console.error("failed to create:", w.name);
      process.exit(1);
    }
  }
}

main().catch((e) => {
  console.error(e.message || e);
  process.exit(1);
});
