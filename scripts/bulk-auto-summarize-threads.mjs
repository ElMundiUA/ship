#!/usr/bin/env node
/**
 * Scan all Linear team issues; for noisy agent-heavy threads, replace with one auto-generated summary + delete rest.
 *
 * Heuristics (avoid wiping human threads):
 * - Skip: 0–1 comments, or single comment already "## Thread summary"
 * - Never clean on comment count alone — requires minimum agent/noise ratio.
 * - Clean if n>=4 and >=55% agent-like; or n>=6 and >=40%; or n>=min-comments and >=35%;
 *   or n in 2–3 and >=90% (tiny all-automation threads).
 *
 * Usage:
 *   node scripts/bulk-auto-summarize-threads.mjs
 *   node scripts/bulk-auto-summarize-threads.mjs --execute
 *   node scripts/bulk-auto-summarize-threads.mjs --execute --min-comments=4
 *   node scripts/bulk-auto-summarize-threads.mjs --execute --issue=ELM-12   # only this number (team from env)
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
/** Lower bound for “heavy thread” tier (still requires agent ratio — never wipe by count alone). */
const MIN_FOR_LONG_THREAD = Math.max(4, parseInt(String(ARGS["min-comments"] || "10"), 10) || 10);
const ONLY_NUMBER = ARGS["issue"] ? parseInt(String(ARGS["issue"]).replace(/^ELM-/i, ""), 10) : null;

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

/** Same buckets as cleanup-agent-comments (subset for scoring). */
function classifyAgentBucket(body) {
  if (!body || typeof body !== "string") return null;
  const m = body.match(/\[GitHub SDLC:\s*([^\]]+)\]/i);
  if (m) return `sdlc:${m[1].trim().toLowerCase()}`;
  if (/^##\s*Ready for Human Validation/im.test(body)) return "human_validation";
  if (/^##\s*Feature Description/im.test(body)) return "feature_description";
  if (/A2 clarification follow-up|Clarification follow-up completed/i.test(body)) return "clarification";
  if (/Intake (review|audit|update) completed|Classification:\s*\*\*/i.test(body)) return "intake";
  if (/^Developer update:/im.test(body)) return "dev_update";
  if (/Preview recovery update|^Preview recovery audit|^Preview failure/i.test(body)) return "preview_recovery";
  if (/^Superseded by /im.test(body)) return "superseded";
  if (/\[LINEAR-DRAFT\]/i.test(body)) return "linear_draft";
  return null;
}

function isAgentLikeBody(body) {
  if (classifyAgentBucket(body)) return true;
  const b = body || "";
  if (/^##\s*Ready for Human Validation/im.test(b)) return true;
  if (/A8 Preview|Preview Failure Recovery|\(A\d+\)/i.test(b)) return true;
  if (/BA spec|BA handoff|BA completion|Intake (pass|triage|audit|finalized|review)/i.test(b)) return true;
  if (/Developer correction:|QA automation|Stuck Issue Sweep/i.test(b)) return true;
  if (/^\*\*Preview validation failed\*\*/im.test(b)) return true;
  return false;
}

function agentFraction(comments) {
  if (!comments.length) return 0;
  const n = comments.filter((c) => isAgentLikeBody(c.body || "")).length;
  return n / comments.length;
}

function alreadyOnlyOurSummary(comments) {
  if (comments.length !== 1) return false;
  const b = (comments[0].body || "").trim();
  return /^##\s*(Thread summary|Сводка треда)/im.test(b);
}

function shouldClean(comments) {
  const n = comments.length;
  if (n < 2) return false;
  if (alreadyOnlyOurSummary(comments)) return false;
  const f = agentFraction(comments);
  if (n >= 4 && f >= 0.55) return true;
  if (n >= 6 && f >= 0.4) return true;
  if (n >= MIN_FOR_LONG_THREAD && f >= 0.35) return true;
  if (n >= 2 && n <= 3 && f >= 0.9) return true;
  return false;
}

function normalizeUrl(u) {
  return u.replace(/[),.;:]+$/g, "");
}

function buildAutoSummary(issue, comments) {
  const today = new Date().toISOString().slice(0, 10);
  const urls = new Set();
  const prs = new Set();
  const commits = new Set();

  for (const c of comments) {
    const b = c.body || "";
    for (const m of b.matchAll(/\bhttps?:\/\/[^\s<>\])'"']+/gi)) {
      urls.add(normalizeUrl(m[0]));
    }
    for (const m of b.matchAll(/\bgithub\.com\/[^\s/]+\/[^\s/]+\/pull\/\d+/gi)) {
      prs.add("https://" + normalizeUrl(m[0]).replace(/^https?:\/\//i, ""));
    }
    for (const m of b.matchAll(/\b(?:commit|commits)\/[a-f0-9]{7,40}\b/gi)) {
      const h = m[0].split("/").pop();
      if (h && h.length >= 7) commits.add(h.slice(0, 12));
    }
    for (const m of b.matchAll(/\b[a-f0-9]{40}\b/gi)) commits.add(m[0].slice(0, 12));
  }

  for (const p of prs) urls.delete(p);

  const lines = [
    "## Thread summary (auto-archive)",
    "",
    `**Issue:** ${issue.identifier} — ${issue.title}`,
    `**Status:** ${issue.state.name} (${issue.state.type})`,
    "",
    `Original thread: **${comments.length}** comments, collapsed **${today}**. Full spec and AC live in the **issue description**; this is only a digest of links/artifacts from the removed thread.`,
    "",
  ];

  if (prs.size) {
    lines.push("**Pull requests:**");
    for (const p of [...prs].sort()) lines.push(`- ${p}`);
    lines.push("");
  }

  const shortCommits = [...commits].filter((c) => c.length <= 12).slice(0, 20);
  if (shortCommits.length) {
    lines.push("**Commit references (partial list):**");
    lines.push(shortCommits.sort().join(", "));
    lines.push("");
  }

  const restUrls = [...urls].sort().slice(0, 35);
  if (restUrls.length) {
    lines.push("**Other links:**");
    for (const u of restUrls) lines.push(`- ${u}`);
    lines.push("");
  }

  if (!prs.size && !restUrls.length && !shortCommits.length) {
    lines.push("_No recognized URLs/PRs/hashes in the thread — use the issue description._");
    lines.push("");
  }

  return lines.join("\n").trim();
}

async function allIssues(apiKey, teamId) {
  const out = [];
  let cursor = null;
  for (;;) {
    const data = await graphql(
      apiKey,
      `query($teamId: ID!, $after: String) {
        issues(filter: { team: { id: { eq: $teamId } } }, first: 100, after: $after) {
          pageInfo { hasNextPage endCursor }
          nodes {
            id
            identifier
            title
            number
            state { name type }
          }
        }
      }`,
      { teamId, after: cursor }
    );
    const conn = data.issues;
    out.push(...conn.nodes);
    if (!conn.pageInfo.hasNextPage) break;
    cursor = conn.pageInfo.endCursor;
  }
  return out;
}

async function allCommentsForIssue(apiKey, teamId, number) {
  let cursor = null;
  const comments = [];
  let issue = null;
  for (;;) {
    const data = await graphql(
      apiKey,
      `query($f: IssueFilter!, $after: String) {
        issues(filter: $f, first: 1) {
          nodes {
            id
            identifier
            title
            state { name type }
            comments(first: 100, after: $after) {
              pageInfo { hasNextPage endCursor }
              nodes { id createdAt body }
            }
          }
        }
      }`,
      { f: { team: { id: { eq: teamId } }, number: { eq: number } }, after: cursor }
    );
    const iss = data.issues?.nodes?.[0];
    if (!iss) return null;
    issue = {
      id: iss.id,
      identifier: iss.identifier,
      title: iss.title,
      state: iss.state,
    };
    const conn = iss.comments;
    comments.push(...conn.nodes);
    if (!conn.pageInfo.hasNextPage) break;
    cursor = conn.pageInfo.endCursor;
  }
  return { issue, comments };
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

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function main() {
  const dot = loadEnv();
  const apiKey = process.env.LINEAR_API_KEY || dot.LINEAR_API_KEY;
  if (!apiKey) throw new Error("LINEAR_API_KEY required");
  const teamKey = process.env.LINEAR_TEAM_KEY || dot.LINEAR_TEAM_KEY || "ELM";

  const teamsData = await graphql(apiKey, `query { teams(first: 50) { nodes { id key } } }`);
  const team =
    teamsData.teams?.nodes?.find((t) => t.key?.toLowerCase() === teamKey.toLowerCase()) ??
    teamsData.teams?.nodes?.[0];
  if (!team) throw new Error("Team not found");

  let issues = await allIssues(apiKey, team.id);
  if (ONLY_NUMBER != null && !Number.isNaN(ONLY_NUMBER)) {
    issues = issues.filter((i) => i.number === ONLY_NUMBER);
  }
  issues.sort((a, b) => a.number - b.number);

  const plan = [];
  for (const meta of issues) {
    const pack = await allCommentsForIssue(apiKey, team.id, meta.number);
    if (!pack) continue;
    const { issue, comments } = pack;
    const n = comments.length;
    const f = agentFraction(comments);
    const clean = shouldClean(comments);
    plan.push({ issue, comments, n, f, clean });
    await sleep(40);
  }

  const todo = plan.filter((p) => p.clean);
  const skip = plan.filter((p) => !p.clean && p.n > 1);

  console.log(`Team ${team.key}: ${plan.length} issues scanned, ${todo.length} to consolidate, ${skip.length} multi-comment issues skipped by heuristics.\n`);

  for (const p of plan) {
    const flag = p.clean ? "CLEAN" : p.n <= 1 ? "ok" : "skip";
    console.log(`${flag}\t${p.issue.identifier}\tcomments=${p.n}\tagent≈${(p.f * 100).toFixed(0)}%\t${p.issue.title.slice(0, 55)}`);
  }

  if (!EXECUTE) {
    console.log(`\nDry-run. ${todo.length} issue(s) would get auto-summary + comment wipe. Pass --execute to apply.`);
    return;
  }

  for (const p of todo) {
    const { issue, comments } = p;
    const body = buildAutoSummary(issue, comments);
    const oldIds = comments.map((c) => c.id);
    try {
      const newId = await addComment(apiKey, issue.id, body);
      let deleted = 0;
      for (const id of oldIds) {
        if (id === newId) continue;
        try {
          await deleteComment(apiKey, id);
          deleted++;
        } catch (e) {
          console.error(`❌ ${issue.identifier} delete ${id}:`, e.message);
        }
        await sleep(25);
      }
      console.log(`✅ ${issue.identifier}: summary ${newId}, deleted ${deleted}`);
    } catch (e) {
      console.error(`❌ ${issue.identifier}:`, e.message);
    }
    await sleep(80);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
