#!/usr/bin/env node
/**
 * Pick one Linear issue: Todo + ready:developer, excluding blocked labels.
 * Prints identifier (e.g. ELM-42) or nothing.
 * In GitHub Actions: exit 1 if LINEAR_API_KEY is missing (fail the job loudly).
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { linearGraphql, resolveTeam, exitIfMissingLinearKeyInCi } from "./lib/linear-fetch.mjs";
import { resolveSdlcProjectId, withSdlcProject } from "./lib/sdlc-project.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const AGENT_DIR = resolve(__dirname, "..");
const ENV_PATH = resolve(AGENT_DIR, ".env");

const EXCLUDE_LABELS = new Set([
  "human:review-required",
  "auto:failed",
  "result:blocked",
]);

function loadEnv() {
  const env = {};
  if (existsSync(ENV_PATH)) {
    for (const line of readFileSync(ENV_PATH, "utf8").split("\n")) {
      const m = line.match(/^([^#=]+)=(.*)$/);
      if (m) env[m[1].trim()] = m[2].trim().replace(/^["']|["']$/g, "");
    }
  }
  return env;
}

function hasExcludedLabel(labels) {
  const names = (labels?.nodes ?? []).map((l) => l.name);
  return names.some((n) => EXCLUDE_LABELS.has(n));
}

function inGithubActions() {
  return process.env.GITHUB_ACTIONS === "true";
}

async function main() {
  const env = loadEnv();
  const apiKey = process.env.LINEAR_API_KEY || env.LINEAR_API_KEY;
  exitIfMissingLinearKeyInCi(apiKey);
  if (!apiKey) {
    process.exit(0);
  }
  const teamKeyOrId = (process.env.LINEAR_TEAM_KEY || env.LINEAR_TEAM_KEY || "ELM").trim() || "ELM";

  const team = await resolveTeam(linearGraphql, apiKey, teamKeyOrId);
  if (!team) {
    console.error(`pick-next-dev: could not resolve team "${teamKeyOrId}"`);
    if (inGithubActions()) process.exit(1);
    process.exit(0);
  }

  const getEnv = (k) => process.env[k] || env[k];
  const projectId = await resolveSdlcProjectId(apiKey, getEnv);
  if (!projectId) {
    console.error("pick-next-dev: could not resolve SDLC project id");
    if (inGithubActions()) process.exit(1);
    process.exit(0);
  }

  const data = await linearGraphql(apiKey, `
    query($filter: IssueFilter!, $first: Int!) {
      issues(filter: $filter, first: $first) {
        nodes {
          identifier
          updatedAt
          state { name }
          labels { nodes { name } }
        }
      }
    }
  `, {
    filter: withSdlcProject(
      {
        team: { id: { eq: team.id } },
        state: { name: { eq: "Todo" } },
        labels: { some: { name: { eq: "ready:developer" } } },
      },
      projectId
    ),
    first: 50,
  });

  let nodes = data.issues?.nodes ?? [];
  nodes = nodes.filter((n) => n.state?.name === "Todo");
  nodes = nodes.filter((n) => !hasExcludedLabel(n.labels));

  nodes.sort((a, b) => new Date(a.updatedAt) - new Date(b.updatedAt));
  const picked = nodes[0];
  if (!picked && inGithubActions()) {
    console.error(
      `pick-next-dev: no issue. Need status Todo + label ready:developer (and not ${[...EXCLUDE_LABELS].join("/")}). Other Todo rows are skipped.`
    );
  }
  process.stdout.write(picked?.identifier ?? "");
}

main().catch((e) => {
  console.error(e.message);
  if (inGithubActions()) process.exit(1);
  process.exit(0);
});
