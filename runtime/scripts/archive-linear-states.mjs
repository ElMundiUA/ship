#!/usr/bin/env node
/**
 * Archive extra Linear workflow states (keep only 6 columns).
 * Usage: node scripts/archive-linear-states.mjs [--dry-run]
 */
import { readFileSync, existsSync } from "node:fs";
import { ENV_PATH } from "./lib/paths.mjs";
const DRY_RUN = process.argv.includes("--dry-run");
const LINEAR_API = "https://api.linear.app/graphql";

const KEEP = ["Backlog", "Todo", "In Progress", "In Review", "Done", "Blocked"];

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

  const teamKeyOrId = env.LINEAR_TEAM_ID || env.LINEAR_TEAM_KEY || "ELM";
  const teamsData = await graphql(apiKey, `query { teams(first: 50) { nodes { id key states { nodes { id name } } } } }`);
  const teams = teamsData.teams?.nodes ?? [];
  const team = teams.find((t) => t.key?.toLowerCase() === String(teamKeyOrId).toLowerCase()) ?? teams[0];
  if (!team) throw new Error("No team found");

  const states = team.states.nodes;
  const toArchive = states.filter((s) => !KEEP.includes(s.name));

  if (toArchive.length === 0) {
    console.log("No extra states to archive.");
    return;
  }

  console.log("Keeping:", KEEP.join(", "));
  console.log("Archiving:", toArchive.map((s) => s.name).join(", "));

  for (const state of toArchive) {
    if (DRY_RUN) {
      console.log(`  [dry] Archive: ${state.name} (${state.id})`);
      continue;
    }
    try {
      await graphql(apiKey, `
        mutation($id: String!) {
          workflowStateArchive(id: $id) { success }
        }
      `, { id: state.id });
      console.log(`  ✅ Archived: ${state.name}`);
    } catch (e) {
      console.error(`  ❌ ${state.name}:`, e.message);
    }
  }
  console.log("\n✅ Done.");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
