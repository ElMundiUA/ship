/**
 * `shipctl knowledge` — manage workspace knowledge buckets.
 *
 * Today this is a thin wrapper around the backend's
 * ``POST /v1/workspaces/{ws}/repos/{repo}/knowledge_seed`` endpoint —
 * the same one the onboarding wizard's step 4 hits. It opens a single
 * PR in the tenant repo that drops starter markdown under
 * ``.ship/knowledge/``:
 *
 *   - ``code-style.md`` — languages, naming, imports, tests, review checklist
 *   - ``ui-runbook.md`` — design-system usage, states, perf budgets
 *
 * The CLI exists so CI pipelines (and re-adoption flows where the UI
 * wizard isn't the natural entry point) can wire the buckets without a
 * browser round-trip.
 *
 * Usage:
 *
 *   shipctl knowledge init [--workspace <id>] [--repo <id>]
 *                          [--only code-style,ui-runbook]
 *                          [--base-url https://api.ship.example.com]
 *                          [--json]
 *
 * Auth: bearer token from ``SHIP_API_TOKEN`` (the same env var the
 * console docs describe for CLI sessions minted under Settings →
 * "Mint a CLI token"). We deliberately don't read ``SHIP_RUN_TOKEN`` —
 * that's a short-lived pipeline handle, not a user PAT.
 *
 * Base URL resolution:
 *
 *   1. ``--base-url`` flag (explicit wins)
 *   2. ``SHIP_WORKSPACE_API_BASE`` (workspace control plane)
 *   3. ``SHIP_API_BASE`` (methodology API; only usable if the caller
 *      ran their own reverse-proxy that co-locates both)
 *   4. ``https://api.ship.elmundi.com`` as the canonical production
 *      workspace API.
 *
 * Workspace + repo resolution:
 *
 *   - ``--workspace`` pins a workspace id; otherwise we fetch
 *     ``GET /v1/workspaces`` and pick the only row. If there are
 *     multiple rows we abort with a helpful message so the caller
 *     either supplies ``--workspace`` or narrows their PAT.
 *   - ``--repo`` pins a repo id (uuid) or a full_name like
 *     ``owner/name``; otherwise we fetch
 *     ``GET /v1/workspaces/{ws}/repos`` and pick the most-recently
 *     activated row — the same heuristic the wizard uses, so
 *     ``shipctl knowledge init`` on a freshly-onboarded workspace
 *     seeds the repo the user just activated.
 */

const VERSION = "v1";

/** Ship ships two starter buckets today — keep in lockstep with
 * ``backend.app.services.catalog.KNOWLEDGE_STARTERS`` and
 * ``console/src/lib/api/client.ts#KNOWLEDGE_STARTERS``. */
const KNOWN_SLUGS = ["code-style", "ui-runbook"];

/**
 * @param {{baseUrl?: string, json?: boolean}} ctx
 * @param {string[]} rest
 */
export async function knowledgeCommand(ctx, rest) {
  const [sub, ...args] = rest;
  if (!sub || sub === "help" || sub === "-h" || sub === "--help") {
    printKnowledgeHelp();
    return;
  }
  if (sub === "init") {
    await knowledgeInitCommand(ctx, args);
    return;
  }
  console.error(
    `Unknown 'shipctl knowledge' subcommand: ${sub}\nRun: shipctl knowledge --help`,
  );
  process.exit(1);
}

function printKnowledgeHelp() {
  console.log(`shipctl knowledge — manage workspace knowledge buckets (${VERSION})

SUBCOMMANDS
  shipctl knowledge init [--workspace <id>] [--repo <id|owner/name>]
                         [--only <csv>] [--json]

INIT FLAGS
  --workspace <id>   Workspace UUID. Defaults to the only workspace
                     the caller's PAT can see.
  --repo <ref>       Workspace repo UUID, or GitHub 'owner/name'.
                     Defaults to the most-recently activated repo in
                     the resolved workspace.
  --only <csv>       Comma-separated starter slugs. Defaults to the
                     full catalog (${KNOWN_SLUGS.join(", ")}).
  --base-url URL     Workspace control-plane API. See env fallbacks.
  --json             Emit a machine-readable JSON summary.

ENV
  SHIP_API_TOKEN             Required. Bearer PAT minted at /settings.
  SHIP_WORKSPACE_API_BASE    Optional override for the control plane.
  SHIP_API_BASE              Fallback only (co-located proxies).

EXIT
  0  PR opened (or idempotently already present)
  1  arg / config error
  2  auth error (401)
  3  network / HTTP 5xx
`);
}

/**
 * @param {{baseUrl?: string, json?: boolean}} ctx
 * @param {string[]} args
 */
async function knowledgeInitCommand(ctx, args) {
  const opts = parseInitArgs(args);
  const baseUrl = resolveBaseUrl(opts.baseUrl || ctx.baseUrl);
  const token = process.env.SHIP_API_TOKEN || "";
  if (!token) {
    console.error(
      "SHIP_API_TOKEN is required. Mint one at /settings in the Ship console.",
    );
    process.exit(1);
  }

  const selection = opts.only;
  if (selection !== null) {
    const unknown = selection.filter((s) => !KNOWN_SLUGS.includes(s));
    if (unknown.length) {
      console.error(
        `Unknown knowledge slug(s): ${unknown.join(", ")}\nKnown: ${KNOWN_SLUGS.join(", ")}`,
      );
      process.exit(1);
    }
  }

  let workspaceId = opts.workspace;
  if (!workspaceId) {
    workspaceId = await resolveSoleWorkspace(baseUrl, token);
  }

  const repoId = await resolveRepoId(baseUrl, token, workspaceId, opts.repo);

  const body = selection === null ? {} : { selection };
  const result = await apiPostJson(
    baseUrl,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/knowledge_seed`,
    body,
    token,
  );

  if (ctx.json || opts.json) {
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  const files = Array.isArray(result.files) ? result.files : [];
  console.log(
    `Seeded knowledge buckets for workspace ${workspaceId} / repo ${repoId}:\n` +
      `  PR #${result.pr_number}: ${result.pr_url}\n` +
      `  Branch: ${result.branch}\n` +
      `  Files: ${files.join(", ") || "(none)"}`,
  );
}

/**
 * @param {string[]} args
 * @returns {{
 *   workspace: string|null,
 *   repo: string|null,
 *   only: string[]|null,
 *   baseUrl: string|null,
 *   json: boolean,
 * }}
 */
function parseInitArgs(args) {
  const out = {
    workspace: null,
    repo: null,
    only: null,
    baseUrl: null,
    json: false,
  };
  const copy = [...args];
  const consume = (flag, key) => {
    if (copy[0] === flag && copy[1] !== undefined) {
      copy.shift();
      out[key] = String(copy.shift());
      return true;
    }
    const p = `${flag}=`;
    if (copy[0] && copy[0].startsWith(p)) {
      out[key] = copy[0].slice(p.length);
      copy.shift();
      return true;
    }
    return false;
  };
  while (copy.length) {
    if (
      consume("--workspace", "workspace") ||
      consume("--repo", "repo") ||
      consume("--only", "only") ||
      consume("--base-url", "baseUrl")
    ) {
      continue;
    }
    if (copy[0] === "--json") {
      out.json = true;
      copy.shift();
      continue;
    }
    if (copy[0] === "--help" || copy[0] === "-h") {
      printKnowledgeHelp();
      process.exit(0);
    }
    console.error(`Unknown flag: ${copy[0]}`);
    process.exit(1);
  }
  if (out.only !== null) {
    out.only = String(out.only)
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return out;
}

/**
 * @param {string|null|undefined} explicit
 * @returns {string}
 */
function resolveBaseUrl(explicit) {
  if (explicit) return explicit.replace(/\/+$/, "");
  const envWorkspace = process.env.SHIP_WORKSPACE_API_BASE;
  if (envWorkspace) return envWorkspace.replace(/\/+$/, "");
  const envGeneric = process.env.SHIP_API_BASE;
  if (envGeneric) return envGeneric.replace(/\/+$/, "");
  return "https://api.ship.elmundi.com";
}

/**
 * @param {string} baseUrl
 * @param {string} token
 * @returns {Promise<string>}
 */
async function resolveSoleWorkspace(baseUrl, token) {
  const rows = await apiGetJson(baseUrl, "/v1/workspaces", token);
  if (!Array.isArray(rows) || rows.length === 0) {
    console.error("No workspaces visible to this token.");
    process.exit(1);
  }
  if (rows.length > 1) {
    const ids = rows.map((r) => `${r.id} (${r.name ?? "?"})`).join("\n  ");
    console.error(
      `Token has access to more than one workspace; pass --workspace <id>.\n  ${ids}`,
    );
    process.exit(1);
  }
  return String(rows[0].id);
}

/**
 * @param {string} baseUrl
 * @param {string} token
 * @param {string} workspaceId
 * @param {string|null} hint
 * @returns {Promise<string>}
 */
async function resolveRepoId(baseUrl, token, workspaceId, hint) {
  // Direct UUID? Accept it verbatim — avoids a list call.
  if (hint && /^[0-9a-fA-F-]{32,36}$/.test(hint) && hint.includes("-")) {
    return hint;
  }
  const rows = await apiGetJson(
    baseUrl,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos`,
    token,
  );
  if (!Array.isArray(rows) || rows.length === 0) {
    console.error(
      `Workspace ${workspaceId} has no activated repos. Activate one in the console first.`,
    );
    process.exit(1);
  }
  if (hint) {
    const match = rows.find(
      (r) =>
        r.full_name === hint ||
        `${r.owner ?? ""}/${r.name ?? ""}` === hint ||
        r.id === hint,
    );
    if (!match) {
      const known = rows.map((r) => r.full_name ?? r.id).join(", ");
      console.error(
        `--repo ${hint} doesn't match any activated repo in workspace ${workspaceId}.\nKnown: ${known}`,
      );
      process.exit(1);
    }
    return String(match.id);
  }
  const sorted = [...rows].sort((a, b) => {
    const ax = a.activated_at ? Date.parse(a.activated_at) : 0;
    const bx = b.activated_at ? Date.parse(b.activated_at) : 0;
    return bx - ax;
  });
  return String(sorted[0].id);
}

/**
 * @param {string} baseUrl
 * @param {string} path
 * @param {string} token
 */
async function apiGetJson(baseUrl, path, token) {
  return apiRequest(baseUrl, path, "GET", token, null);
}

/**
 * @param {string} baseUrl
 * @param {string} path
 * @param {Record<string, unknown>} body
 * @param {string} token
 */
async function apiPostJson(baseUrl, path, body, token) {
  return apiRequest(baseUrl, path, "POST", token, body);
}

/**
 * @param {string} baseUrl
 * @param {string} path
 * @param {string} method
 * @param {string} token
 * @param {Record<string, unknown>|null} body
 */
async function apiRequest(baseUrl, path, method, token, body) {
  const url = `${baseUrl}${path}`;
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: body === null ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    console.error(`Network error calling ${url}: ${err instanceof Error ? err.message : err}`);
    process.exit(3);
  }
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (res.ok) return data;
  if (res.status === 401) {
    console.error(
      `HTTP 401 on ${method} ${url} — SHIP_API_TOKEN is missing, expired, or lacks workspace access.`,
    );
    process.exit(2);
  }
  const msg = typeof data === "string" ? data : JSON.stringify(data);
  console.error(`HTTP ${res.status} ${res.statusText} on ${method} ${url}\n${msg}`);
  process.exit(res.status >= 500 ? 3 : 1);
}
