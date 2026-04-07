#!/usr/bin/env node
/**
 * Setup Linear SDLC workflow: create workflow states and labels.
 * Usage: node scripts/setup-linear-sdlc.mjs [--dry-run]
 * Requires: LINEAR_API_KEY, LINEAR_TEAM_ID or LINEAR_TEAM_KEY in .env
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { RUNTIME_ROOT, ENV_PATH } from "./lib/paths.mjs";

const CONFIG_PATH = resolve(RUNTIME_ROOT, "config/sdlc-workflow.json");

const DRY_RUN = process.argv.includes("--dry-run");
const LINEAR_API = "https://api.linear.app/graphql";

function loadEnv() {
  if (!existsSync(ENV_PATH)) {
    console.error("❌ .env not found. Copy from .env.example and set LINEAR_API_KEY.");
    process.exit(1);
  }
  const content = readFileSync(ENV_PATH, "utf8");
  const env = {};
  for (const line of content.split("\n")) {
    const m = line.match(/^([^#=]+)=(.*)$/);
    if (m) env[m[1].trim()] = m[2].trim().replace(/^["']|["']$/g, "");
  }
  return env;
}

function loadConfig() {
  if (!existsSync(CONFIG_PATH)) {
    console.error("❌ config/sdlc-workflow.json not found.");
    process.exit(1);
  }
  return JSON.parse(readFileSync(CONFIG_PATH, "utf8"));
}

async function graphql(apiKey, query, variables = {}) {
  const res = await fetch(LINEAR_API, {
    method: "POST",
    headers: {
      Authorization: apiKey,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors) throw new Error(JSON.stringify(json.errors));
  return json.data;
}

async function resolveTeamId(apiKey, teamKeyOrId) {
  const data = await graphql(apiKey, `
    query Teams { teams(first: 50) { nodes { id key } } }
  `);
  const teams = data.teams.nodes;
  if (teams.length === 0) throw new Error("No teams found");
  if (teamKeyOrId) {
    const byKey = teams.find((t) => t.key?.toLowerCase() === String(teamKeyOrId).toLowerCase());
    if (byKey) return byKey.id;
    if (teams.some((t) => t.id === teamKeyOrId)) return teamKeyOrId;
  }
  return teams[0].id;
}

async function getExistingWorkflowStates(apiKey, teamId) {
  // Linear: workflow states are under team.states
  const data = await graphql(apiKey, `
    query Team($id: String!) {
      team(id: $id) {
        states { nodes { id name } }
      }
    }
  `, { id: teamId });
  return data.team?.states?.nodes ?? [];
}

async function createWorkflowState(apiKey, teamId, { name, type, color, position }) {
  const data = await graphql(apiKey, `
    mutation CreateState($input: WorkflowStateCreateInput!) {
      workflowStateCreate(input: $input) {
        workflowState { id name }
      }
    }
  `, {
    input: { teamId, name, type, color, position },
  });
  return data.workflowStateCreate?.workflowState;
}

async function getExistingLabels(apiKey, teamId) {
  const data = await graphql(apiKey, `
    query TeamLabels($id: String!) {
      team(id: $id) {
        labels { nodes { id name } }
      }
    }
  `, { id: teamId });
  return data.team?.labels?.nodes ?? [];
}

async function createLabel(apiKey, teamId, name) {
  const data = await graphql(apiKey, `
    mutation CreateLabel($input: IssueLabelCreateInput!) {
      issueLabelCreate(input: $input) {
        issueLabel { id name }
      }
    }
  `, { input: { name, teamId } });
  return data.issueLabelCreate?.issueLabel;
}

async function main() {
  const env = loadEnv();
  const apiKey = env.LINEAR_API_KEY;
  if (!apiKey) {
    console.error("❌ LINEAR_API_KEY not set in .env");
    process.exit(1);
  }

  const teamKeyOrId = env.LINEAR_TEAM_ID || env.LINEAR_TEAM_KEY || "ELM";
  const teamId = await resolveTeamId(apiKey, teamKeyOrId);
  console.log(`Team ID: ${teamId}`);

  const config = loadConfig();
  const statuses = config.workflow.statuses;
  const allLabels = [
    ...(config.labels.stage || []),
    ...(config.labels.routing || []),
    ...(config.labels.exception || []),
    ...(config.labels.quality || []),
  ];

  if (DRY_RUN) {
    console.log("\n[DRY RUN] Would create:");
    console.log("  Workflow states:", statuses.map((s) => s.name).join(", "));
    console.log("  Labels:", allLabels.join(", "));
    return;
  }

  // 1. Workflow states
  const existingStates = await getExistingWorkflowStates(apiKey, teamId);
  const existingNames = new Set(existingStates.map((s) => s.name));
  console.log("\n--- Workflow states ---");
  for (const s of statuses) {
    if (existingNames.has(s.name)) {
      console.log(`  ⏭️  ${s.name} (exists)`);
    } else {
      try {
        const created = await createWorkflowState(apiKey, teamId, s);
        if (created) {
          console.log(`  ✅ ${s.name}`);
          existingNames.add(s.name);
        }
      } catch (e) {
        console.error(`  ❌ ${s.name}:`, e.message);
      }
    }
  }

  // 2. Labels
  const existingLabels = await getExistingLabels(apiKey, teamId);
  const existingLabelNames = new Set(existingLabels.map((l) => l.name));
  console.log("\n--- Labels ---");
  for (const name of allLabels) {
    if (existingLabelNames.has(name)) {
      console.log(`  ⏭️  ${name} (exists)`);
    } else {
      try {
        const created = await createLabel(apiKey, teamId, name);
        if (created) {
          console.log(`  ✅ ${name}`);
          existingLabelNames.add(name);
        }
      } catch (e) {
        console.error(`  ❌ ${name}:`, e.message);
      }
    }
  }

  console.log("\n✅ SDLC workflow setup complete.");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
