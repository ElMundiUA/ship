/**
 * `shipctl knowledge` — read-only access to workspace knowledge buckets.
 *
 * The agent calls this during a routine run to pull bucket articles
 * into context (the same surface the Navigator chat reads). Bucket
 * authoring, ingestion, and intel harvest live server-side now;
 * starter docs are written by the wizard at workspace seed time.
 *
 * Usage:
 *
 *   shipctl knowledge fetch <bucket-slug> [--workspace <id>] [--json]
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
 * Workspace resolution:
 *
 *   - ``--workspace`` pins a workspace id; ``SHIP_WORKSPACE_ID`` env
 *     var serves as a fallback. Without either we fetch
 *     ``GET /v1/workspaces`` and pick the only row. If there are
 *     multiple rows we abort with a helpful message.
 */

const VERSION = "v2";

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
  if (sub === "fetch") {
    await knowledgeFetchCommand(ctx, args);
    return;
  }
  console.error(
    `Unknown 'shipctl knowledge' subcommand: ${sub}\nRun: shipctl knowledge --help`,
  );
  process.exit(1);
}

function printKnowledgeHelp() {
  console.log(`shipctl knowledge — read workspace knowledge buckets (${VERSION})

SUBCOMMANDS
  shipctl knowledge fetch <bucket-slug> [--workspace <id>] [--json]

ENV
  SHIP_API_TOKEN             Required. Bearer PAT minted at /settings.
  SHIP_WORKSPACE_ID          Optional. Skips the /v1/workspaces lookup.
  SHIP_WORKSPACE_API_BASE    Optional override for the control plane.
  SHIP_API_BASE              Fallback only (co-located proxies).

EXIT
  0  bucket fetched
  1  arg / config error
  2  auth error (401)
  3  network / HTTP 5xx
`);
}

async function knowledgeFetchCommand(ctx, args) {
  const opts = parseFetchArgs(args);
  const baseUrl = resolveBaseUrl(opts.baseUrl || explicitGlobalBaseUrl(ctx));
  const token = requireToken();
  let workspaceId =
    opts.workspace || (process.env.SHIP_WORKSPACE_ID || "").trim() || "";
  if (!workspaceId) {
    workspaceId = await resolveSoleWorkspace(baseUrl, token);
  }

  const [bucket, articles, sources] = await Promise.all([
    apiGetJson(
      baseUrl,
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/buckets/${encodeURIComponent(opts.slug)}`,
      token,
    ),
    apiGetJson(
      baseUrl,
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/buckets/${encodeURIComponent(opts.slug)}/articles`,
      token,
    ),
    apiGetJson(
      baseUrl,
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/buckets/${encodeURIComponent(opts.slug)}/sources`,
      token,
    ),
  ]);

  const result = { bucket, articles, sources };
  if (ctx.json || opts.json) {
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  console.log(`${bucket.name} (${bucket.slug})`);
  console.log(`  scope: ${bucket.scope_kind}  source: ${bucket.source_kind}`);
  console.log(`  articles: ${Array.isArray(articles) ? articles.length : 0}`);
  console.log(`  sources: ${Array.isArray(sources) ? sources.length : 0}`);
  for (const article of Array.isArray(articles) ? articles : []) {
    console.log(`\n## ${article.title} (${article.slug})`);
    console.log(String(article.body_md || "").trim());
  }
}

function explicitGlobalBaseUrl(ctx) {
  return ctx?.baseUrlSource === "flag" ? ctx.baseUrl : null;
}

function parseFetchArgs(args) {
  const out = {
    slug: null,
    workspace: null,
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
    if (!String(copy[0]).startsWith("-") && out.slug === null) {
      out.slug = String(copy.shift());
      continue;
    }
    console.error(`Unknown flag: ${copy[0]}`);
    process.exit(1);
  }
  if (!out.slug) {
    console.error(
      "Usage: shipctl knowledge fetch <bucket-slug> [--workspace <id>] [--json]",
    );
    process.exit(1);
  }
  return out;
}

function requireToken() {
  const token = process.env.SHIP_API_TOKEN || "";
  if (!token) {
    console.error(
      "SHIP_API_TOKEN is required. Mint one at /settings in the Ship console.",
    );
    process.exit(1);
  }
  return token;
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
 * @param {string} path
 * @param {string} token
 */
async function apiGetJson(baseUrl, path, token) {
  return apiRequest(baseUrl, path, "GET", token, null);
}

/**
 * @param {string} baseUrl
 * @param {string} path
 * @param {string} method
 * @param {string} token
 * @param {Record<string, unknown>|null} body
 */
// Same retry policy as ``cli/lib/agent_api.mjs`` and
// ``commands/trigger.mjs`` — Bunny edge 502/503/504 + network errors
// are transient; three attempts with exponential backoff cover the
// typical recovery window. 4xx + other 5xx still exit on the first
// attempt so a real bug surfaces fast.
const _RETRY_DELAYS_MS = [500, 1500, 4500];
const _TRANSIENT_STATUSES = new Set([502, 503, 504]);


async function apiRequest(baseUrl, path, method, token, body) {
  const url = `${baseUrl}${path}`;
  for (let attempt = 0; attempt <= _RETRY_DELAYS_MS.length; attempt += 1) {
    if (attempt > 0) {
      await new Promise((r) => setTimeout(r, _RETRY_DELAYS_MS[attempt - 1]));
    }
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
      const message = err instanceof Error ? err.message : String(err);
      if (attempt < _RETRY_DELAYS_MS.length) {
        console.error(
          `warn: network error on ${method} ${url} (attempt ${attempt + 1}/${_RETRY_DELAYS_MS.length + 1}): ${message}`,
        );
        continue;
      }
      console.error(`Network error calling ${url}: ${message}`);
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
    if (
      _TRANSIENT_STATUSES.has(res.status)
      && attempt < _RETRY_DELAYS_MS.length
    ) {
      console.error(
        `warn: transient ${res.status} on ${method} ${url} (attempt ${attempt + 1}/${_RETRY_DELAYS_MS.length + 1}); retrying`,
      );
      continue;
    }
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
  console.error(`apiRequest exhausted retries for ${url}`);
  process.exit(3);
}
