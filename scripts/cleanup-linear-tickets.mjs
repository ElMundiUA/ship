#!/usr/bin/env node
/**
 * Migrate issues from old workflow states to new 6-column scheme.
 * Usage: node scripts/cleanup-linear-tickets.mjs [--dry-run]
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const AGENT_DIR = resolve(__dirname, "..");
const CONFIG_PATH = resolve(AGENT_DIR, "config/sdlc-workflow.json");
const ENV_PATH = resolve(AGENT_DIR, ".env");
const DRY_RUN = process.argv.includes("--dry-run");
const LINEAR_API = "https://api.linear.app/graphql";

function loadEnv() {
  if (!existsSync(ENV_PATH)) throw new Error(".env not found");
  const env = {};
  for (const line of readFileSync(ENV_PATH, "utf8").split("\n")) {
    const m = line.match(/^([^#=]+)=(.*)$/);
    if (m) env[m[1].trim()] = m[2].trim().replace(/^["']|["']$/g, "");
  }
  return env;
}

async function graphql(apiKey, query, variables = {}) {
  const res = await fetch(LINEAR_API, {
    method: "POST",
    headers: { Authorization: apiKey, "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors) throw new Error(JSON.stringify(json.errors));
  return json.data;
}

async function main() {
  const env = loadEnv();
  const apiKey = env.LINEAR_API_KEY;
  if (!apiKey) throw new Error("LINEAR_API_KEY not set");

  const config = JSON.parse(readFileSync(CONFIG_PATH, "utf8"));
  const mapping = config.workflow?.stateMapping || {};
  const teamKeyOrId = env.LINEAR_TEAM_ID || env.LINEAR_TEAM_KEY || "ELM";

  const teamsData = await graphql(apiKey, `query { teams(first: 50) { nodes { id key states { nodes { id name } } } } }`);
  const teams = teamsData.teams?.nodes ?? [];
  const team = teams.find((t) => t.key?.toLowerCase() === String(teamKeyOrId).toLowerCase()) ?? teams[0];
  if (!team) throw new Error("No team found");

  const stateByName = Object.fromEntries(team.states.nodes.map((s) => [s.name, s]));
  const oldStates = Object.keys(mapping);
  const filter = { team: { id: { eq: team.id } }, state: { name: { in: oldStates } } };

  const res = await graphql(apiKey, `
    query($filter: IssueFilter!, $first: Int!) {
      issues(filter: $filter, first: $first) {
        nodes { id identifier title state { id name } }
      }
    }
  `, { filter, first: 100 });

  const issues = res.issues?.nodes ?? [];
  console.log(`Found ${issues.length} issues in old states`);
  if (issues.length === 0) {
    console.log("Nothing to migrate.");
    return;
  }

  for (const issue of issues) {
    const oldState = issue.state?.name;
    const newState = mapping[oldState] || oldState;
    const targetId = stateByName[newState]?.id;
    if (!targetId) {
      console.log(`  ⚠️  ${issue.identifier}: no target for ${oldState} → ${newState}`);
      continue;
    }
    if (DRY_RUN) {
      console.log(`  [dry] ${issue.identifier}: ${oldState} → ${newState}`);
      continue;
    }
    try {
      await graphql(apiKey, `
        mutation($id: String!, $stateId: String!) {
          issueUpdate(id: $id, input: { stateId: $stateId }) { success }
        }
      `, { id: issue.id, stateId: targetId });
      console.log(`  ✅ ${issue.identifier}: ${oldState} → ${newState}`);
    } catch (e) {
      console.error(`  ❌ ${issue.identifier}:`, e.message);
    }
  }
  console.log("\n✅ Cleanup complete.");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
