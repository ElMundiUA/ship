import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  isKnownKnowledgeStarterSlug,
  RECIPE_KNOWLEDGE_PREFIX,
  STATIC_KNOWLEDGE_SLUGS,
} from "../lib/commands/knowledge.mjs";
import {
  renderSpecialistPromptGuardrails,
  SPECIALIST_PROMPT_GUARDRAILS,
} from "../lib/process/specialist-prompt-contract.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BIN = path.resolve(__dirname, "..", "bin", "shipctl.mjs");

function runCtl(args, env = {}) {
  return spawnSync(process.execPath, [BIN, ...args], {
    encoding: "utf8",
    env: { ...process.env, ...env },
  });
}

test("knowledge init accepts static starters and generated recipe slugs", () => {
  assert.deepEqual(STATIC_KNOWLEDGE_SLUGS, ["code-style", "ui-runbook"]);
  assert.equal(isKnownKnowledgeStarterSlug("code-style"), true);
  assert.equal(isKnownKnowledgeStarterSlug("ui-runbook"), true);
  assert.equal(
    isKnownKnowledgeStarterSlug(`${RECIPE_KNOWLEDGE_PREFIX}flow-pr-self-review`),
    true,
  );
  assert.equal(isKnownKnowledgeStarterSlug("role-ba"), false);
  assert.equal(isKnownKnowledgeStarterSlug("flow-pr-self-review"), false);
});

test("knowledge init rejects unknown non-recipe slugs before network calls", () => {
  const result = runCtl(["knowledge", "init", "--only", "role-ba"], {
    SHIP_API_TOKEN: "test-token",
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Unknown knowledge slug/);
  assert.match(result.stderr, /ship-recipes\//);
});

test("specialist prompt guardrails require knowledge search and Ship boundaries", () => {
  const rendered = renderSpecialistPromptGuardrails();
  assert.equal(rendered, `${SPECIALIST_PROMPT_GUARDRAILS.trim()}\n`);
  assert.match(rendered, /Before inventing/);
  assert.match(rendered, /Do not perform direct ticket-system mutations/);
  assert.match(rendered, /Repository changes must be delivered through pull requests only/);
});
