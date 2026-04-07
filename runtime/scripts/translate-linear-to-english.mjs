#!/usr/bin/env node
/**
 * Translate Linear issue titles, descriptions, and comments to English when Cyrillic is detected.
 *
 * Requires: LINEAR_API_KEY, OPENAI_API_KEY (in tools/linear-agent/.env or env)
 *
 * Usage:
 *   node scripts/translate-linear-to-english.mjs              # dry-run: scan only (no OpenAI)
 *   node scripts/translate-linear-to-english.mjs --apply       # translate + write (needs OPENAI_API_KEY)
 *   node scripts/translate-linear-to-english.mjs --apply --limit=5
 *   node scripts/translate-linear-to-english.mjs --apply --issue=ELM-49
 */
import { readFileSync, existsSync } from "node:fs";
import { ENV_PATH } from "./lib/paths.mjs";
const LINEAR_API = "https://api.linear.app/graphql";
const OPENAI_API = "https://api.openai.com/v1/chat/completions";

const CYR = /[\u0400-\u04FF]/;

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

function parseArgs() {
  const out = { dryRun: true, limit: 0, issue: "" };
  for (const a of process.argv.slice(2)) {
    if (a === "--apply") out.dryRun = false;
    if (a === "--dry-run") out.dryRun = true;
    if (a.startsWith("--limit=")) out.limit = parseInt(a.slice(8), 10) || 0;
    if (a.startsWith("--issue=")) out.issue = a.slice(8).trim();
  }
  return out;
}

function parseIssueNumber(identifier) {
  const m = String(identifier).match(/^ELM-(\d+)$/i);
  return m ? parseInt(m[1], 10) : 0;
}

function needsTranslate(text) {
  if (!text || !String(text).trim()) return false;
  return CYR.test(text);
}

async function linearGql(apiKey, query, variables = {}) {
  const res = await fetch(LINEAR_API, {
    method: "POST",
    headers: { Authorization: apiKey, "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors) throw new Error(JSON.stringify(json.errors));
  return json.data;
}

async function translateText(openaiKey, text, context) {
  const res = await fetch(OPENAI_API, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${openaiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "gpt-4o-mini",
      temperature: 0.2,
      response_format: { type: "json_object" },
      messages: [
        {
          role: "system",
          content:
            "You translate project management text to clear English. Preserve Markdown, code fences, URLs, issue IDs like ELM-12, and [GitHub SDLC:...] markers. Return JSON: {\"translation\":\"...\"} only.",
        },
        {
          role: "user",
          content: `Context: ${context}\n\nTranslate to English:\n\n${text}`,
        },
      ],
    }),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`OpenAI HTTP ${res.status}: ${err.slice(0, 500)}`);
  }
  const data = await res.json();
  const raw = data.choices?.[0]?.message?.content;
  if (!raw) throw new Error("OpenAI empty response");
  const parsed = JSON.parse(raw);
  const t = parsed.translation;
  if (typeof t !== "string" || !t.trim()) throw new Error("OpenAI bad JSON translation");
  return t.trim();
}

async function listAllIssueIds(apiKey, teamKey, limit) {
  const ids = [];
  let after = null;
  for (;;) {
    const data = await linearGql(
      apiKey,
      `query($after: String, $team: String!) {
        issues(
          first: 100
          after: $after
          filter: { team: { key: { eq: $team } } }
        ) {
          pageInfo { hasNextPage endCursor }
          nodes { id identifier }
        }
      }`,
      { after, team: teamKey }
    );
    for (const n of data.issues.nodes) {
      ids.push(n);
      if (limit > 0 && ids.length >= limit) return ids;
    }
    if (!data.issues.pageInfo.hasNextPage) break;
    after = data.issues.pageInfo.endCursor;
  }
  return ids;
}

async function getIssueDetail(apiKey, issueId) {
  return linearGql(
    apiKey,
    `query($id: String!) {
      issue(id: $id) {
        id
        identifier
        title
        description
        comments(first: 250) {
          nodes { id body }
        }
      }
    }`,
    { id: issueId }
  );
}

async function issueUpdate(apiKey, id, input) {
  return linearGql(
    apiKey,
    `mutation($id: String!, $input: IssueUpdateInput!) {
      issueUpdate(id: $id, input: $input) { success }
    }`,
    { id, input }
  );
}

async function commentUpdate(apiKey, id, body) {
  return linearGql(
    apiKey,
    `mutation($id: String!, $input: CommentUpdateInput!) {
      commentUpdate(id: $id, input: $input) { success }
    }`,
    { id, input: { body } }
  );
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function main() {
  const dot = loadEnv();
  const args = parseArgs();
  const linearKey = process.env.LINEAR_API_KEY || dot.LINEAR_API_KEY;
  const openaiKey = process.env.OPENAI_API_KEY || dot.OPENAI_API_KEY;
  const teamKey = (process.env.LINEAR_TEAM_KEY || dot.LINEAR_TEAM_KEY || "ELM").trim() || "ELM";

  if (!linearKey) {
    console.error("Set LINEAR_API_KEY");
    process.exit(1);
  }
  if (!args.dryRun && !openaiKey) {
    console.error(
      "Set OPENAI_API_KEY in tools/linear-agent/.env (or env). Translation uses OpenAI gpt-4o-mini."
    );
    process.exit(1);
  }

  let targets = [];
  if (args.issue) {
    const num = parseIssueNumber(args.issue);
    if (!num) {
      console.error("Use --issue=ELM-42");
      process.exit(1);
    }
    const data = await linearGql(
      linearKey,
      `query($f: IssueFilter!) {
        issues(filter: $f, first: 1) { nodes { id identifier } }
      }`,
      {
        f: {
          team: { key: { eq: teamKey } },
          number: { eq: num },
        },
      }
    );
    const n = data.issues?.nodes?.[0];
    if (!n) {
      console.error("Issue not found:", args.issue);
      process.exit(1);
    }
    targets = [n];
  } else {
    targets = await listAllIssueIds(linearKey, teamKey, args.limit);
  }

  const modeLabel = args.dryRun ? "[DRY RUN] Cyrillic scan (no OpenAI)" : "Translating";
  console.log(`${modeLabel} — ${targets.length} issue(s)\n`);

  let issuesTouched = 0;
  let commentsTouched = 0;
  let commentsNeeding = 0;

  for (const t of targets) {
    const detail = await getIssueDetail(linearKey, t.id);
    const issue = detail.issue;
    if (!issue) continue;

    const idStr = issue.identifier;

    if (args.dryRun) {
      const tNeed = needsTranslate(issue.title);
      const dNeed = issue.description && needsTranslate(issue.description);
      const comments = issue.comments?.nodes ?? [];
      const cNeed = comments.filter((c) => needsTranslate(c.body));
      if (tNeed || dNeed || cNeed.length) {
        console.log(`${idStr}: title=${tNeed ? "CYR" : "—"} desc=${dNeed ? "CYR" : "—"} comments=${cNeed.length}`);
        commentsNeeding += cNeed.length;
      }
      continue;
    }

    const titleEn = needsTranslate(issue.title)
      ? await translateText(openaiKey, issue.title, `Issue ${idStr} title`)
      : null;
    const descEn =
      issue.description && needsTranslate(issue.description)
        ? await translateText(openaiKey, issue.description, `Issue ${idStr} description`)
        : null;

    if (titleEn || descEn) {
      await issueUpdate(linearKey, issue.id, {
        ...(titleEn ? { title: titleEn } : {}),
        ...(descEn ? { description: descEn } : {}),
      });
      issuesTouched++;
      console.log(`  updated issue ${idStr} (title/desc)`);
      await sleep(400);
    }

    const comments = issue.comments?.nodes ?? [];
    for (const c of comments) {
      if (!needsTranslate(c.body)) continue;
      const bodyEn = await translateText(openaiKey, c.body, `Comment on ${idStr}`);
      await commentUpdate(linearKey, c.id, bodyEn);
      commentsTouched++;
      console.log(`  updated comment on ${idStr}`);
      await sleep(400);
    }
    await sleep(200);
  }

  if (args.dryRun) {
    console.log(`\nComments with Cyrillic (total): ${commentsNeeding}`);
    console.log("Add OPENAI_API_KEY, then: node scripts/translate-linear-to-english.mjs --apply");
  } else {
    console.log(`\nDone. Issues updated: ${issuesTouched}, comments: ${commentsTouched}.`);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
