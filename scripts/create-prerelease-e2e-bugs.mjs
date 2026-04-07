#!/usr/bin/env node
/**
 * One-off / reusable: create ElMundi pre-release Linear issues (known E2E-on-dev gaps).
 * Includes: billing.subscribed, catalog keyboard, tag filter, audio-access, Paddle overlay.
 * Re-running may create duplicates — search Linear before repeat.
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const AGENT_DIR = resolve(__dirname, "..");
const ENV_PATH = resolve(AGENT_DIR, ".env");
const LINEAR_API = "https://api.linear.app/graphql";

const PROJECT_ID = "2eead1a7-8585-4678-96e9-6b3f86b6534c"; // ElMundi pre-release
const TEAM_ID = "98facea5-36fb-44a5-bb47-c651f3ee4073"; // ELM
const STATE_ID = "e2e6345e-dbb4-4423-a68f-5b51cf99d57e"; // Backlog
const BUG_LABEL_ID = "37f890ba-e654-4d2d-811b-06a27777286c"; // Bug

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
  if (json.errors) throw new Error(JSON.stringify(json.errors, null, 2));
  return json.data;
}

const ISSUES = [
  {
    title: "[E2E dev] billing.subscribed: Manage Subscription button missing on /billing",
    description: `## Context
Playwright regression against **https://dev.elmundi.com** (\`PLAYWRIGHT_BASE_URL\` + \`MAILOSAUR_*\`, \`E2E_SUBSCRIBER_EMAIL\`).

## File
\`website/tests/e2e/regression/billing.subscribed.functional.spec.ts\`

## Symptom
After OTP login as subscriber, \`getByRole('button', { name: 'Manage Subscription' })\` is not found — 30s timeout.

## Hypotheses
- \`E2E_SUBSCRIBER_EMAIL\` has no active Paddle subscription on dev / drift vs \`/api/users/me/subscription\`.
- Different billing UI on dev for this state.

## AC
- [ ] On dev, test subscriber sees active subscription and “Manage Subscription” CTA on \`/billing\` (or update test/fixture to match real UI).
`,
  },
  {
    title: "[E2E dev] catalog.functional: keyboard navigation does not reach /category/…",
    description: `## Context
Regression against **dev.elmundi.com**.

## File
\`website/tests/e2e/regression/catalog.functional.spec.ts\` — test **cards are keyboard-activatable**.

## Symptom
After keyboard activation, \`toHaveURL(/\\/category\\//)\` fails: stays on \`https://dev.elmundi.com/\`.

## Hypotheses
- Focus / roving tabindex / Enter differ from local behaviour.
- Timing or dev build differences.

## AC
- [ ] Keyboard activation of the first catalog card navigates to category page on dev (or adjust test to real a11y flow).
`,
  },
  {
    title: "[E2E dev] category-tag-filter: after selecting tag URL stays on /",
    description: `## Context
Regression against **dev.elmundi.com**.

## File
\`website/tests/e2e/regression/category-tag-filter.functional.spec.ts\`

## Symptom
Expected category URL; actually remains home (\`/\`).

## Hypotheses
- No suitable tags/catalog data on dev for the scenario.
- Navigation regression when filtering by tags.

## AC
- [ ] “Tag → category with filtered playlist” works on dev, or test is tied to stable fixtures.
`,
  },
  {
    title: "[E2E dev] subscribed-user.audio-access: 403 and modal blocks catalog",
    description: `## Context
Regression against **dev.elmundi.com** (\`E2E_SUBSCRIBER_EMAIL\`).

## File
\`website/tests/e2e/regression/subscribed-user.audio-access.spec.ts\`

## Symptom
- Console: **403** on resource (CDN / signed URL).
- Catalog card click: \`auth_modal\` intercepts pointer (\`plan__features\` / subscription), timeout on \`openFirstCard\`.

## Hypotheses
- Bunny / entitlement on dev for subscriber.
- Auto-opened subscription modal after login blocks the scenario.

## AC
- [ ] Subscriber on dev has no paywall on audio content; signed URL not 403 for entitled episode.
- [ ] E2E stable (close modal / wait) or backend/CDN fixed.
`,
  },
  {
    title: "[E2E dev] Paddle: Start Free Trial on /pricing does not open overlay (Playwright)",
    description: `## Context
After tuning paddle-checkout.sandbox.functional.spec.ts (PLAYWRIGHT_BASE_URL, profile wait, broader Paddle URL match, .first() for CTA), runs against **https://dev.elmundi.com** may fail: no iframe and no matching network request.

## File
\`website/tests/e2e/regression/paddle-checkout.sandbox.functional.spec.ts\`

## Hypotheses
- **https://dev.elmundi.com** not added as origin in Paddle Checkout (sandbox)
- /api/paddle/token or price IDs error on dev
- Overlay host differs from what the test asserts

## AC
- [ ] Sandbox checkout opens from dev after OTP (Mailosaur) + annual CTA click
- [ ] E2E green with PLAYWRIGHT_BASE_URL=https://dev.elmundi.com or documented exception
`,
  },
];

async function main() {
  const env = loadEnv();
  const apiKey = env.LINEAR_API_KEY;
  if (!apiKey) throw new Error("LINEAR_API_KEY not set");

  const mutation = `
    mutation CreateIssue($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue { identifier title url }
      }
    }
  `;

  for (const item of ISSUES) {
    const data = await graphql(apiKey, mutation, {
      input: {
        teamId: TEAM_ID,
        projectId: PROJECT_ID,
        stateId: STATE_ID,
        title: item.title,
        description: item.description,
        labelIds: [BUG_LABEL_ID],
      },
    });
    const issue = data.issueCreate?.issue;
    if (!issue) throw new Error("issueCreate failed: " + JSON.stringify(data));
    console.log(issue.identifier, issue.url);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
