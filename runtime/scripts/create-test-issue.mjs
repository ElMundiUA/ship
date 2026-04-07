#!/usr/bin/env node
/**
 * Create a test Linear issue in Backlog for SDLC flow verification.
 */
import { readFileSync, existsSync } from "node:fs";
import { ENV_PATH } from "./lib/paths.mjs";
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

  const teamKeyOrId = env.LINEAR_TEAM_ID || env.LINEAR_TEAM_KEY || "ELM";
  const teamsData = await graphql(apiKey, `
    query Teams { teams(first: 50) { nodes { id key states { nodes { id name } } } } }
  `);
  const teams = teamsData.teams?.nodes ?? [];
  const team = teams.find((t) => t.key?.toLowerCase() === String(teamKeyOrId).toLowerCase());
  const teamRes = team ?? teams[0];
  if (!teamRes) throw new Error("No teams found");

  const backlogState = teamRes.states.nodes.find((s) => s.name === "Backlog");
  if (!backlogState) throw new Error("Backlog state not found");

  const result = await graphql(apiKey, `
    mutation CreateIssue($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        issue { id identifier title url }
      }
    }
  `, {
    input: {
      teamId: teamRes.id,
      stateId: backlogState.id,
      title: "[SDLC Test] Add a simple footer link to /about",
      description: `Test issue for SDLC automation flow.

## Problem
Footer lacks a link to the about page.

## Goal
Add "About" link in footer.

## Expected
User can click footer link and reach /about.

## Acceptance Criteria
- [ ] Footer has "About" link
- [ ] Link navigates to /about
`,
    },
  });

  const issue = result.issueCreate?.issue;
  if (!issue) throw new Error("Failed to create issue");

  console.log("Created:", issue.identifier, issue.title);
  console.log("URL:", issue.url);
  return issue;
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
