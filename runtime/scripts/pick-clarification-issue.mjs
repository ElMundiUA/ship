#!/usr/bin/env node
/**
 * Todo + needs:clarification + SDLC Linear project. Skip if last comment looks like our bot or too fresh.
 */
import { readFileSync, existsSync } from "node:fs";
import { linearGraphql, resolveTeam, exitIfMissingLinearKeyInCi } from "./lib/linear-fetch.mjs";
import { resolveSdlcProjectId, withSdlcProject } from "./lib/sdlc-project.mjs";
import { ENV_PATH } from "./lib/paths.mjs";
const COOLDOWN_MS = 45 * 60 * 1000;
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

function looksLikeAgentComment(body) {
  if (!body) return false;
  return SDLC_MARKERS.some((m) => body.includes(m));
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
          id
          identifier
          updatedAt
          labels { nodes { name } }
          comments(first: 3) { nodes { body createdAt } }
        }
      }
    }
  `, {
    filter: withSdlcProject(
      {
        team: { id: { eq: team.id } },
        state: { name: { eq: "Todo" } },
        labels: { some: { name: { eq: "needs:clarification" } } },
      },
      projectId
    ),
    first: 50,
  });

  let nodes = data.issues?.nodes ?? [];
  const now = Date.now();
  nodes = nodes.filter((n) => {
    const last = n.comments?.nodes?.[0];
    if (!last) return true;
    const age = now - new Date(last.createdAt).getTime();
    if (age < COOLDOWN_MS && looksLikeAgentComment(last.body)) return false;
    return true;
  });
  nodes.sort((a, b) => new Date(a.updatedAt) - new Date(b.updatedAt));
  process.stdout.write(nodes[0]?.identifier ?? "");
}

main().catch((e) => {
  console.error(e.message);
  process.exit(0);
});
