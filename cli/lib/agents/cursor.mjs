/**
 * Cursor Cloud Agent runtime adapter.
 *
 * Wraps the public ``POST https://api.cursor.com/v0/agents`` API the
 * ElMundi sibling repo uses (``tools/linear-agent/scripts/cloud-agent-launch.mjs``).
 * The shape Ship needs:
 *
 *   1. Launch an agent against ``repo`` at ``ref`` with ``prompt``.
 *   2. Poll until the agent's status is one of the terminal values
 *      (``FINISHED`` / ``ERRORED`` / ``CANCELLED``).
 *   3. Return the branch name + final status to ``shipctl run``. Side
 *      effects (tracker writes / inbox rows) happen via the agent's
 *      own ``POST /agent-runs/finish`` call from inside Cursor — the
 *      CLI no longer reads a state file off the branch.
 *
 * Auth: ``CURSOR_API_KEY`` env var, sent as Basic ``<key>:`` per Cursor's
 * docs.
 */

import { Buffer } from "node:buffer";

const BASE = "https://api.cursor.com";
const TERMINAL_STATUSES = new Set(["FINISHED", "ERRORED", "CANCELLED"]);


function authHeader() {
  const key = (process.env.CURSOR_API_KEY || "").trim();
  if (!key) {
    throw new Error("CURSOR_API_KEY is not set");
  }
  return "Basic " + Buffer.from(`${key}:`).toString("base64");
}


async function postJson(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: authHeader(),
    },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`Cursor API ${path} ${res.status}: ${text.slice(0, 500)}`);
  }
  return text ? JSON.parse(text) : {};
}


async function getJson(path) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Authorization: authHeader() },
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`Cursor API ${path} ${res.status}: ${text.slice(0, 500)}`);
  }
  return text ? JSON.parse(text) : {};
}


/**
 * Launch a single Cursor agent and poll until it terminates.
 *
 * @param {object} opts
 * @param {string} opts.repoUrl       e.g. ``https://github.com/owner/repo``
 * @param {string} opts.ref           branch the agent checks out (default ``main``)
 * @param {string} opts.branchName    branch the agent commits onto
 * @param {string} opts.prompt        full prompt body
 * @param {boolean} [opts.autoCreatePr] open a PR when done (default false)
 * @param {number} [opts.pollIntervalMs]   poll cadence (default 15s)
 * @param {number} [opts.timeoutMs]   total deadline (default 30 min)
 * @param {(line: string) => void} [opts.onLog] streaming log hook
 * @returns {Promise<{ agentId: string, branchName: string, status: string, raw: object }>}
 */
export async function runCursorAgent({
  repoUrl,
  ref = "main",
  branchName,
  prompt,
  autoCreatePr = false,
  pollIntervalMs = 15_000,
  timeoutMs = 30 * 60 * 1000,
  onLog = (l) => console.error(`[cursor] ${l}`),
} = {}) {
  if (!repoUrl) throw new Error("runCursorAgent: repoUrl required");
  if (!branchName) throw new Error("runCursorAgent: branchName required");
  if (!prompt || typeof prompt !== "string") {
    throw new Error("runCursorAgent: prompt required");
  }

  const launch = await postJson("/v0/agents", {
    prompt: { text: prompt },
    source: { repository: repoUrl, ref },
    target: {
      branchName,
      autoCreatePr,
      openAsCursorGithubApp: false,
    },
  });
  const agentId = launch.id || launch.agentId || launch.agent_id;
  if (!agentId) {
    throw new Error(`Cursor launch returned no agent id: ${JSON.stringify(launch).slice(0, 300)}`);
  }
  onLog(`launched agent=${agentId} branch=${branchName} ref=${ref}`);

  const deadline = Date.now() + timeoutMs;
  let lastStatus = launch.status || "CREATING";
  while (Date.now() < deadline) {
    if (TERMINAL_STATUSES.has(String(lastStatus).toUpperCase())) {
      onLog(`agent terminal: status=${lastStatus}`);
      return { agentId, branchName, status: lastStatus, raw: launch };
    }
    await sleep(pollIntervalMs);
    let snapshot;
    try {
      snapshot = await getJson(`/v0/agents/${encodeURIComponent(agentId)}`);
    } catch (err) {
      onLog(`poll error (continuing): ${err.message}`);
      continue;
    }
    const next = (snapshot.status || "").toString();
    if (next && next !== lastStatus) {
      onLog(`status: ${lastStatus} → ${next}`);
      lastStatus = next;
    }
    if (TERMINAL_STATUSES.has(next.toUpperCase())) {
      return { agentId, branchName, status: next, raw: snapshot };
    }
  }
  throw new Error(
    `Cursor agent ${agentId} did not reach a terminal state within ${Math.round(
      timeoutMs / 1000,
    )}s (last status: ${lastStatus})`,
  );
}


function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
