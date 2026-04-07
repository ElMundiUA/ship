#!/usr/bin/env node
/**
 * Remove duplicate / spam agent comments on Linear issues (keep newest per category).
 *
 * Usage:
 *   node scripts/cleanup-agent-comments.mjs --dry-run
 *   node scripts/cleanup-agent-comments.mjs --execute
 *   node scripts/cleanup-agent-comments.mjs --execute --issue=ELM-59
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ENV_PATH = resolve(__dirname, "../.env");
const LINEAR_API = "https://api.linear.app/graphql";

const ARGS = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, v] = a.startsWith("--") ? a.slice(2).split("=", 2) : [a, true];
    return [k, v === true ? true : v ?? true];
  })
);

const DRY_RUN = ARGS["dry-run"] !== undefined || !ARGS["execute"];
const ONLY_ISSUE = ARGS["issue"] || "";

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

/** Returns bucket key or null if comment should not be touched */
function classifyBody(body) {
  if (!body || typeof body !== "string") return null;
  const m = body.match(/\[GitHub SDLC:\s*([^\]]+)\]/i);
  if (m) return `sdlc:${m[1].trim().toLowerCase()}`;
  if (/^##\s*Ready for Human Validation/im.test(body)) return "block:human_validation";
  if (/^##\s*Feature Description/im.test(body)) return "block:feature_description";
  if (/A2 clarification follow-up|Clarification follow-up completed/i.test(body)) return "block:clarification";
  if (/Intake (review|audit|update) completed|Classification:\s*\*\*/i.test(body)) return "block:intake";
  if (/^Developer update:/im.test(body)) return "block:dev_update";
  if (/Preview recovery update|^Preview recovery audit/i.test(body)) return "block:preview_recovery";
  if (/^Superseded by /im.test(body)) return "block:superseded";
  if (/\[LINEAR-DRAFT\]/i.test(body)) return "block:linear_draft";
  return null;
}

async function deleteComment(apiKey, id) {
  await graphql(apiKey, `mutation($id: String!) { commentDelete(id: $id) { success } }`, { id });
}

async function main() {
  const dot = loadEnv();
  const apiKey = process.env.LINEAR_API_KEY || dot.LINEAR_API_KEY;
  if (!apiKey) throw new Error("LINEAR_API_KEY required");
  const teamKey = process.env.LINEAR_TEAM_KEY || dot.LINEAR_TEAM_KEY || "ELM";

  const teamsData = await graphql(apiKey, `query { teams(first: 50) { nodes { id key } } }`);
  const teams = teamsData.teams?.nodes ?? [];
  const team = teams.find((t) => t.key?.toLowerCase() === teamKey.toLowerCase()) ?? teams[0];
  if (!team) throw new Error("Team not found");

  let issues = [];
  if (ONLY_ISSUE) {
    const parsed = ONLY_ISSUE.match(/^([A-Za-z]+)-(\d+)$/);
    if (!parsed) throw new Error("Use --issue=ELM-59");
    const num = parseInt(parsed[2], 10);
    const key = parsed[1].toUpperCase();
    const res = await graphql(apiKey, `
      query($f: IssueFilter!) {
        issues(filter: $f, first: 1) {
          nodes { id identifier comments(first: 250) { nodes { id body createdAt } } }
        }
      }
    `, { f: { team: { id: { eq: team.id } }, number: { eq: num } } });
    issues = res.issues?.nodes ?? [];
  } else {
    const res = await graphql(apiKey, `
      query($teamId: ID!) {
        issues(
          filter: { team: { id: { eq: $teamId } }, state: { type: { nin: ["completed", "canceled"] } } }
          first: 80
          orderBy: updatedAt
        ) {
          nodes { id identifier comments(first: 250) { nodes { id body createdAt } } }
        }
      }
    `, { teamId: team.id });
    issues = res.issues?.nodes ?? [];
  }

  let wouldDelete = 0;
  let deleted = 0;

  for (const issue of issues) {
    const comments = issue.comments?.nodes ?? [];
    if (comments.length < 2) continue;

    const byBucket = new Map();
    for (const c of comments) {
      const bucket = classifyBody(c.body || "");
      if (!bucket) continue;
      if (!byBucket.has(bucket)) byBucket.set(bucket, []);
      byBucket.get(bucket).push(c);
    }

    const toRemove = [];
    for (const [, list] of byBucket) {
      if (list.length < 2) continue;
      list.sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt));
      // keep newest, remove older
      for (let i = 0; i < list.length - 1; i++) {
        toRemove.push(list[i]);
      }
    }

    for (const item of toRemove) {
      const preview = (item.body || "").replace(/\s+/g, " ").slice(0, 80);
      wouldDelete++;
      if (DRY_RUN) {
        console.log(`[dry-run] ${issue.identifier} delete ${item.id} … ${preview}`);
      } else {
        try {
          await deleteComment(apiKey, item.id);
          deleted++;
          console.log(`✅ deleted ${issue.identifier} ${item.id}`);
        } catch (e) {
          console.error(`❌ ${issue.identifier} ${item.id}:`, e.message);
        }
      }
    }
  }

  console.log(DRY_RUN ? `\nDry-run: ${wouldDelete} comment(s) would be deleted. Run with --execute to apply.` : `\nDeleted ${deleted} comment(s).`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
