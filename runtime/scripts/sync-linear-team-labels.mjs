#!/usr/bin/env node
/**
 * Ensure SDLC labels exist on ELM; migrate stage:dev → stage:developer; drop empty cruft labels.
 * Usage: node scripts/sync-linear-team-labels.mjs [--dry-run]
 * Requires LINEAR_API_KEY (+ LINEAR_TEAM_KEY) in tools/linear-agent/.env
 */
import { readFileSync, existsSync } from "node:fs";
import { ENV_PATH } from "./lib/paths.mjs";
const DRY_RUN = process.argv.includes("--dry-run");
const LINEAR_API = "https://api.linear.app/graphql";

/** Labels required by linear-agent (dist/agent-contracts + SDLC pick scripts + prompts). */
const REQUIRED_LABELS = [
  // SDLC routing
  "needs:clarification",
  "stage:intake",
  "ready:developer",
  "ready:qa",
  "ready:human",
  "stage:developer",
  "human:review-required",
  "auto:failed",
  "auto:retry",
  "infra:deployment",
  // daily audit roles (Linear issues created by scheduled Cloud Agent)
  "source:tech-architect",
  "source:qa-architect",
  "source:security-officer",
  "audit:auto",
  // agent-contracts
  ...[
    "stage:ba",
    "stage:bug-agent",
    "stage:architect",
    "stage:qa-architect",
    "stage:qa-automation",
    "stage:release-manager",
    "ready:ba",
    "ready:bug-agent",
    "ready:architect",
    "ready:qa-architect",
    "ready:qa-automation",
    "ready:release-manager",
    "result:passed",
    "result:failed",
    "result:blocked",
    "result:needs-human",
    "result:skipped",
    "flow:no-ba",
    "flow:bug",
    "flow:hotfix",
    "flow:manual-merge-required",
    "flow:preview-required",
    "flow:release-candidate",
  ],
];

/** Legacy labels to remove only if zero issues (after migration). */
const DELETE_IF_EMPTY = [
  "ci:failed",
  "blocked",
  "risk:high",
  "risk:low",
  "risk:medium",
  "stage:dev",
];

function loadEnv() {
  if (!existsSync(ENV_PATH)) {
    console.error("Missing .env");
    process.exit(1);
  }
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

async function resolveTeamId(apiKey, teamKeyOrId) {
  const data = await graphql(apiKey, `query { teams(first: 50) { nodes { id key } } }`);
  const teams = data.teams.nodes;
  const key = String(teamKeyOrId || "ELM").trim() || "ELM";
  const team = teams.find((t) => t.key?.toLowerCase() === key.toLowerCase());
  if (team) return team.id;
  if (teams.length === 1) return teams[0].id;
  throw new Error(`Team not found: ${key}`);
}

async function createLabel(apiKey, teamId, name) {
  const data = await graphql(
    apiKey,
    `mutation CreateLabel($input: IssueLabelCreateInput!) {
      issueLabelCreate(input: $input) { issueLabel { id name } }
    }`,
    { input: { name, teamId } }
  );
  return data.issueLabelCreate?.issueLabel;
}

async function deleteLabel(apiKey, labelId) {
  const data = await graphql(
    apiKey,
    `mutation DeleteLabel($id: String!) {
      issueLabelDelete(id: $id) { success }
    }`,
    { id: labelId }
  );
  return data.issueLabelDelete?.success === true;
}

async function issueLabelAdd(apiKey, issueId, labelId) {
  await graphql(
    apiKey,
    `mutation AddLabel($id: String!, $labelId: String!) {
      issueAddLabel(id: $id, labelId: $labelId) { success }
    }`,
    { id: issueId, labelId }
  );
}

async function issueLabelRemove(apiKey, issueId, labelId) {
  await graphql(
    apiKey,
    `mutation RemoveLabel($id: String!, $labelId: String!) {
      issueRemoveLabel(id: $id, labelId: $labelId) { success }
    }`,
    { id: issueId, labelId }
  );
}

async function countIssuesWithLabel(apiKey, teamId, labelId) {
  const filter = { team: { id: { eq: teamId } }, labels: { some: { id: { eq: labelId } } } };
  let total = 0;
  let after = null;
  for (;;) {
    const data = await graphql(
      apiKey,
      `query Q($f: IssueFilter!, $after: String) {
        issues(filter: $f, first: 100, after: $after) {
          nodes { id }
          pageInfo { hasNextPage endCursor }
        }
      }`,
      { f: filter, after }
    );
    total += data.issues.nodes.length;
    if (!data.issues.pageInfo.hasNextPage) break;
    after = data.issues.pageInfo.endCursor;
  }
  return total;
}

async function listIssuesWithLabel(apiKey, teamId, labelId, limit = 200) {
  const filter = { team: { id: { eq: teamId } }, labels: { some: { id: { eq: labelId } } } };
  const data = await graphql(
    apiKey,
    `query Q($f: IssueFilter!, $n: Int!) {
      issues(filter: $f, first: $n) { nodes { id identifier } }
    }`,
    { f: filter, n: limit }
  );
  return data.issues.nodes;
}

async function main() {
  const env = loadEnv();
  const apiKey = env.LINEAR_API_KEY;
  if (!apiKey) {
    console.error("LINEAR_API_KEY required");
    process.exit(1);
  }
  const teamId = await resolveTeamId(apiKey, env.LINEAR_TEAM_KEY || env.LINEAR_TEAM_ID || "ELM");
  const teamLabelData = await graphql(
    apiKey,
    `query Q($id: String!) { team(id: $id) { key labels(first: 250) { nodes { id name } } } }`,
    { id: teamId }
  );
  const teamKey = teamLabelData.team.key;
  const existing = teamLabelData.team.labels.nodes;
  const byName = new Map(existing.map((l) => [l.name, l]));

  console.log(`Team ${teamKey} (${teamId})\n`);

  const required = [...new Set(REQUIRED_LABELS)].sort();

  // 1) Create missing
  console.log("--- Create required labels ---");
  for (const name of required) {
    if (byName.has(name)) {
      console.log(`  ⏭️  ${name}`);
      continue;
    }
    if (DRY_RUN) {
      console.log(`  [dry-run] would create ${name}`);
      continue;
    }
    const created = await createLabel(apiKey, teamId, name);
    if (created) {
      byName.set(name, created);
      console.log(`  ✅ ${name}`);
    }
  }

  // 2) Migrate stage:dev → stage:developer
  const stageDev = byName.get("stage:dev");
  const stageDeveloper = byName.get("stage:developer");
  if (stageDev && stageDeveloper) {
    const issues = await listIssuesWithLabel(apiKey, teamId, stageDev.id);
    console.log(`\n--- Migrate stage:dev → stage:developer (${issues.length} issues) ---`);
    for (const n of issues) {
      const hasDev = await graphql(
        apiKey,
        `query($id: String!) { issue(id: $id) { labels { nodes { name } } } }`,
        { id: n.id }
      );
      const has = (hasDev.issue?.labels?.nodes ?? []).some((l) => l.name === "stage:developer");
      if (DRY_RUN) {
        console.log(`  [dry-run] ${n.identifier}: add stage:developer=${!has}, remove stage:dev`);
        continue;
      }
      if (!has) {
        await issueLabelAdd(apiKey, n.id, stageDeveloper.id);
      }
      await issueLabelRemove(apiKey, n.id, stageDev.id);
      console.log(`  ✅ ${n.identifier}`);
    }
  }

  // 3) After migration, stage:dev should be empty — try delete again
  if (stageDev && stageDeveloper && !DRY_RUN) {
    const left = await countIssuesWithLabel(apiKey, teamId, stageDev.id);
    if (left === 0) {
      const ok = await deleteLabel(apiKey, stageDev.id);
      console.log(`\n--- stage:dev label removed (${ok ? "ok" : "failed"}) ---`);
      byName.delete("stage:dev");
    } else {
      console.log(`\n⚠️  stage:dev still has ${left} issues after migration`);
    }
  }

  // 4) Delete empty labels from DELETE_IF_EMPTY
  console.log("\n--- Remove empty deprecated labels ---");
  for (const name of DELETE_IF_EMPTY) {
    const lbl = byName.get(name);
    if (!lbl) {
      console.log(`  ⏭️  ${name} (not present)`);
      continue;
    }
    const cnt = await countIssuesWithLabel(apiKey, teamId, lbl.id);
    if (cnt > 0) {
      console.log(`  ⚠️  ${name}: ${cnt} issues — skip delete`);
      continue;
    }
    if (DRY_RUN) {
      console.log(`  [dry-run] would delete ${name}`);
      continue;
    }
    const ok = await deleteLabel(apiKey, lbl.id);
    console.log(ok ? `  🗑️  ${name}` : `  ❌ ${name} delete failed`);
  }

  console.log("\n✅ Done.");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
