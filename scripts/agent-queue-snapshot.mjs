#!/usr/bin/env node
/**
 * SDLC queue snapshot (same filters as pick-* scripts).
 * Run: node scripts/agent-queue-snapshot.mjs
 * JSON: node scripts/agent-queue-snapshot.mjs --json
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { linearGraphql, resolveTeam } from "./lib/linear-fetch.mjs";
import { resolveSdlcProjectId, withSdlcProject } from "./lib/sdlc-project.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ENV_PATH = resolve(__dirname, "../.env");

const EXCLUDE_DEV = new Set(["human:review-required", "auto:failed", "result:blocked"]);
const CLARIFY_COOLDOWN_MS = 45 * 60 * 1000;
const SDLC_MARKERS = ["[GitHub SDLC:", "[SDLC:", "Clarification Agent", "Intake Agent", "BA/Spec Agent"];

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

function hasExcludedDev(labels) {
  const names = (labels?.nodes ?? []).map((l) => l.name);
  return names.some((n) => EXCLUDE_DEV.has(n));
}

function looksLikeAgentComment(body) {
  if (!body) return false;
  return SDLC_MARKERS.some((m) => body.includes(m));
}

function sortByUpdated(nodes) {
  return [...nodes].sort((a, b) => new Date(a.updatedAt) - new Date(b.updatedAt));
}

async function main() {
  const jsonOut = process.argv.includes("--json");
  const dot = loadDotenv();
  const apiKey = process.env.LINEAR_API_KEY || dot.LINEAR_API_KEY;
  const teamKey = process.env.LINEAR_TEAM_KEY || dot.LINEAR_TEAM_KEY || "ELM";
  if (!apiKey) {
    console.error("Set LINEAR_API_KEY or tools/linear-agent/.env");
    process.exit(1);
  }
  const team = await resolveTeam(linearGraphql, apiKey, teamKey);
  if (!team) {
    console.error("Team not found:", teamKey);
    process.exit(1);
  }

  const getEnv = (k) => process.env[k] || dot[k];
  const projectId = await resolveSdlcProjectId(apiKey, getEnv);
  if (!projectId) {
    console.error(
      "Could not resolve SDLC Linear project — set LINEAR_SDLC_PROJECT_ID or LINEAR_SDLC_PROJECT_NAME (see .env.example)."
    );
    process.exit(1);
  }

  const qTodoLane = await linearGraphql(
    apiKey,
    `query($filter: IssueFilter!, $first: Int!) {
      issues(filter: $filter, first: $first) {
        nodes { identifier updatedAt title labels { nodes { name } } }
      }
    }`,
    {
      filter: withSdlcProject(
        { team: { id: { eq: team.id } }, state: { name: { eq: "Todo" } } },
        projectId
      ),
      first: 100,
    }
  );
  const todoLane = qTodoLane.issues?.nodes ?? [];

  const intakePool = sortByUpdated(
    todoLane.filter(
      (n) =>
        !hasLabel(n.labels, "stage:intake") &&
        !hasLabel(n.labels, "needs:clarification") &&
        !hasLabel(n.labels, "ready:developer")
    )
  );

  const qClar = await linearGraphql(
    apiKey,
    `query($filter: IssueFilter!, $first: Int!) {
      issues(filter: $filter, first: $first) {
        nodes {
          identifier updatedAt title
          labels { nodes { name } }
          comments(first: 3) { nodes { body createdAt } }
        }
      }
    }`,
    {
      filter: withSdlcProject(
        {
          team: { id: { eq: team.id } },
          state: { name: { eq: "Todo" } },
          labels: { some: { name: { eq: "needs:clarification" } } },
        },
        projectId
      ),
      first: 50,
    }
  );
  const now = Date.now();
  const clarAll = qClar.issues?.nodes ?? [];
  const clarEligible = sortByUpdated(
    clarAll.filter((n) => {
      const last = n.comments?.nodes?.[0];
      if (!last) return true;
      const age = now - new Date(last.createdAt).getTime();
      if (age < CLARIFY_COOLDOWN_MS && looksLikeAgentComment(last.body)) return false;
      return true;
    })
  );

  const baPool = sortByUpdated(
    todoLane.filter(
      (n) =>
        hasLabel(n.labels, "stage:intake") &&
        !hasLabel(n.labels, "needs:clarification") &&
        !hasLabel(n.labels, "ready:developer")
    )
  );

  const qTodo = await linearGraphql(
    apiKey,
    `query($filter: IssueFilter!, $first: Int!) {
      issues(filter: $filter, first: $first) {
        nodes {
          identifier updatedAt title
          state { name }
          labels { nodes { name } }
        }
      }
    }`,
    {
      filter: withSdlcProject(
        {
          team: { id: { eq: team.id } },
          state: { name: { eq: "Todo" } },
          labels: { some: { name: { eq: "ready:developer" } } },
        },
        projectId
      ),
      first: 100,
    }
  );
  let devPool = qTodo.issues?.nodes ?? [];
  devPool = devPool.filter((n) => n.state?.name === "Todo" && !hasExcludedDev(n.labels));
  devPool = sortByUpdated(devPool);

  const take = (arr, n = 8) => arr.slice(0, n).map((x) => x.identifier);

  const snapshot = {
    team: team.key,
    sdlcProjectId: projectId,
    generatedAt: new Date().toISOString(),
    queues: {
      intake: { count: intakePool.length, nextPick: intakePool[0]?.identifier ?? null, sample: take(intakePool) },
      clarification: {
        countTotal: clarAll.length,
        countEligibleForPick: clarEligible.length,
        nextPick: clarEligible[0]?.identifier ?? null,
        sample: take(clarEligible),
      },
      ba: { count: baPool.length, nextPick: baPool[0]?.identifier ?? null, sample: take(baPool) },
      developer: { count: devPool.length, nextPick: devPool[0]?.identifier ?? null, sample: take(devPool) },
    },
    note:
      "Scheduled workflows usually take one ticket per slot per role (~2h). To process more the same day, use workflow_dispatch with issue=TICKET-XX.",
  };

  if (jsonOut) {
    console.log(JSON.stringify(snapshot, null, 2));
    return;
  }

  const { queues: q } = snapshot;
  console.log(`\n=== SDLC queue snapshot — ${team.key} — ${snapshot.generatedAt} ===\n`);
  console.log(`Intake (Todo + SDLC project, no stage:intake / needs:clarification / ready:developer): ${q.intake.count}`);
  console.log(`  next pick: ${q.intake.nextPick ?? "—"}`);
  console.log(`  sample: ${q.intake.sample.join(", ") || "—"}\n`);
  console.log(`Clarification: ${q.clarification.countTotal} total, ${q.clarification.countEligibleForPick} eligible (cooldown)`);
  console.log(`  next pick: ${q.clarification.nextPick ?? "—"}`);
  console.log(`  sample: ${q.clarification.sample.join(", ") || "—"}\n`);
  console.log(`BA (Todo + stage:intake): ${q.ba.count}`);
  console.log(`  next pick: ${q.ba.nextPick ?? "—"}`);
  console.log(`  sample: ${q.ba.sample.join(", ") || "—"}\n`);
  console.log(`Developer (Todo + ready:developer): ${q.developer.count}`);
  console.log(`  next pick: ${q.developer.nextPick ?? "—"}`);
  console.log(`  sample: ${q.developer.sample.join(", ") || "—"}\n`);
  console.log(snapshot.note + "\n");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
