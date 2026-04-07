#!/usr/bin/env node
/**
 * Post one summary comment on a Linear issue, then delete all previous comments.
 *
 * Usage:
 *   node scripts/replace-thread-with-summary.mjs --issue=ELM-59 --summary=scripts/summaries/ELM-59.thread-summary.md --dry-run
 *   node scripts/replace-thread-with-summary.mjs --issue=ELM-59 --summary=scripts/summaries/ELM-59.thread-summary.md --execute
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

const EXECUTE = ARGS["execute"] === true || ARGS["execute"] === "";
const ISSUE = ARGS["issue"] || "";
const SUMMARY_PATH = ARGS["summary"] || "";

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
  if (json.errors) throw new Error(JSON.stringify(json.errors, null, 2));
  return json.data;
}

async function allCommentIds(apiKey, teamId, number) {
  let cursor = null;
  const ids = [];
  let issueId = null;
  let identifier = null;
  for (;;) {
    const data = await graphql(
      apiKey,
      `query($f: IssueFilter!, $after: String) {
        issues(filter: $f, first: 1) {
          nodes {
            id
            identifier
            comments(first: 100, after: $after) {
              pageInfo { hasNextPage endCursor }
              nodes { id }
            }
          }
        }
      }`,
      { f: { team: { id: { eq: teamId } }, number: { eq: number } }, after: cursor }
    );
    const iss = data.issues?.nodes?.[0];
    if (!iss) throw new Error(`Issue not found for number ${number}`);
    issueId = iss.id;
    identifier = iss.identifier;
    const conn = iss.comments;
    for (const n of conn.nodes) ids.push(n.id);
    if (!conn.pageInfo.hasNextPage) break;
    cursor = conn.pageInfo.endCursor;
  }
  return { issueId, identifier, commentIds: ids };
}

async function addComment(apiKey, issueId, body) {
  const data = await graphql(
    apiKey,
    `mutation($issueId: String!, $body: String!) {
      commentCreate(input: { issueId: $issueId, body: $body }) {
        success
        comment { id }
      }
    }`,
    { issueId, body }
  );
  const id = data.commentCreate?.comment?.id;
  if (!data.commentCreate?.success || !id) throw new Error("commentCreate failed");
  return id;
}

async function deleteComment(apiKey, id) {
  await graphql(apiKey, `mutation($id: String!) { commentDelete(id: $id) { success } }`, { id });
}

async function main() {
  const parsed = ISSUE.match(/^([A-Za-z]+)-(\d+)$/);
  if (!parsed) throw new Error("Use --issue=ELM-59");
  const number = parseInt(parsed[2], 10);

  if (!SUMMARY_PATH) throw new Error("Use --summary=path/to.md");
  const fromCwd = resolve(process.cwd(), SUMMARY_PATH);
  const fromScript = resolve(__dirname, SUMMARY_PATH);
  const summaryFile = existsSync(fromCwd) ? fromCwd : existsSync(fromScript) ? fromScript : null;
  if (!summaryFile) throw new Error(`Summary file not found: ${SUMMARY_PATH}`);
  const body = readFileSync(summaryFile, "utf8");

  const dot = loadEnv();
  const apiKey = process.env.LINEAR_API_KEY || dot.LINEAR_API_KEY;
  if (!apiKey) throw new Error("LINEAR_API_KEY required");
  const teamKey = process.env.LINEAR_TEAM_KEY || dot.LINEAR_TEAM_KEY || "ELM";

  const teamsData = await graphql(apiKey, `query { teams(first: 50) { nodes { id key } } }`);
  const team =
    teamsData.teams?.nodes?.find((t) => t.key?.toLowerCase() === teamKey.toLowerCase()) ??
    teamsData.teams?.nodes?.[0];
  if (!team) throw new Error("Team not found");

  const { issueId, identifier, commentIds } = await allCommentIds(apiKey, team.id, number);

  if (!EXECUTE) {
    console.log(`[dry-run] ${identifier}: would add 1 summary comment, delete ${commentIds.length} existing`);
    console.log(body.slice(0, 400) + (body.length > 400 ? "…" : ""));
    return;
  }

  const newId = await addComment(apiKey, issueId, body.trim());
  console.log(`✅ ${identifier}: posted summary comment ${newId}`);

  let deleted = 0;
  for (const id of commentIds) {
    if (id === newId) continue;
    try {
      await deleteComment(apiKey, id);
      deleted++;
    } catch (e) {
      console.error(`❌ delete ${id}:`, e.message);
    }
  }
  console.log(`✅ ${identifier}: deleted ${deleted} prior comment(s)`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
