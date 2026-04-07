#!/usr/bin/env node
/**
 * Todo (human moved issue into the lane), project ElMundi pre-release, no stage:intake — oldest first.
 * Backlog is manual-only; automation starts when you move a ticket to Todo.
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { linearGraphql, resolveTeam, exitIfMissingLinearKeyInCi } from "./lib/linear-fetch.mjs";
import { resolveSdlcProjectId, withSdlcProject } from "./lib/sdlc-project.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ENV_PATH = resolve(__dirname, "../.env");

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

function hasLabel(labels, name) {
  return (labels?.nodes ?? []).some((l) => l.name === name);
}

async function main() {
  const dot = loadDotenv();
  const apiKey = process.env.LINEAR_API_KEY || dot.LINEAR_API_KEY;
  const teamKey = process.env.LINEAR_TEAM_KEY || dot.LINEAR_TEAM_KEY || "ELM";
  exitIfMissingLinearKeyInCi(apiKey);
  if (!apiKey) {
    process.exit(0);
  }
  const team = await resolveTeam(linearGraphql, apiKey, teamKey);
  if (!team) return;

  const getEnv = (k) => process.env[k] || dot[k];
  const projectId = await resolveSdlcProjectId(apiKey, getEnv);
  if (!projectId) return;

  const data = await linearGraphql(apiKey, `
    query($filter: IssueFilter!, $first: Int!) {
      issues(filter: $filter, first: $first) {
        nodes {
          identifier
          updatedAt
          labels { nodes { name } }
        }
      }
    }
  `, {
    filter: withSdlcProject(
      {
        team: { id: { eq: team.id } },
        state: { name: { eq: "Todo" } },
      },
      projectId
    ),
    first: 50,
  });

  let nodes = data.issues?.nodes ?? [];
  nodes = nodes.filter((n) => !hasLabel(n.labels, "stage:intake"));
  nodes = nodes.filter((n) => !hasLabel(n.labels, "needs:clarification"));
  nodes = nodes.filter((n) => !hasLabel(n.labels, "ready:developer"));
  nodes.sort((a, b) => new Date(a.updatedAt) - new Date(b.updatedAt));
  process.stdout.write(nodes[0]?.identifier ?? "");
}

main().catch((e) => {
  console.error(e.message);
  process.exit(0);
});
