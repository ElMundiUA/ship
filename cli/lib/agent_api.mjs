/**
 * Shared client helpers for ``shipctl`` commands that hit the
 * agent-runs API (inbox / project / etc).
 *
 * Reuses the same auth model the existing ``shipctl knowledge`` flow
 * established: bearer ``SHIP_API_TOKEN`` PAT, base URL resolved via
 * the same precedence chain (flag → ``SHIP_WORKSPACE_API_BASE`` →
 * ``SHIP_API_BASE`` → prod default), workspace id resolved via flag /
 * ``SHIP_WORKSPACE_ID`` / sole-workspace lookup.
 *
 * Exit codes are uniform across commands so wrapper scripts can
 * branch on them: 0 ok, 1 arg/config error, 2 auth error (401),
 * 3 network/5xx.
 */

const PROD_BASE = "https://api.ship.elmundi.com";


/**
 * @param {string|null|undefined} explicit
 * @returns {string}
 */
export function resolveBaseUrl(explicit) {
  if (explicit) return explicit.replace(/\/+$/, "");
  const envWorkspace = process.env.SHIP_WORKSPACE_API_BASE;
  if (envWorkspace) return envWorkspace.replace(/\/+$/, "");
  const envGeneric = process.env.SHIP_API_BASE;
  if (envGeneric) return envGeneric.replace(/\/+$/, "");
  return PROD_BASE;
}


export function requireToken() {
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
 * @param {string} baseUrl
 * @param {string} token
 * @returns {Promise<string>}
 */
export async function resolveSoleWorkspace(baseUrl, token) {
  const rows = await apiRequest(baseUrl, "/v1/workspaces", "GET", token, null);
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
 * @param {{ workspace?: string|null, baseUrl?: string|null }} opts
 * @param {{ baseUrl?: string|null, baseUrlSource?: string|null }} ctx
 */
export async function resolveContext(opts, ctx) {
  const baseUrl = resolveBaseUrl(
    opts.baseUrl || (ctx?.baseUrlSource === "flag" ? ctx?.baseUrl : null),
  );
  const token = requireToken();
  let workspaceId =
    opts.workspace || (process.env.SHIP_WORKSPACE_ID || "").trim() || "";
  if (!workspaceId) {
    workspaceId = await resolveSoleWorkspace(baseUrl, token);
  }
  return { baseUrl, token, workspaceId };
}


/**
 * @param {string} baseUrl
 * @param {string} path
 * @param {"GET"|"POST"|"DELETE"} method
 * @param {string} token
 * @param {Record<string, unknown>|null} body
 */
export async function apiRequest(baseUrl, path, method, token, body) {
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
    console.error(
      `Network error calling ${url}: ${err instanceof Error ? err.message : err}`,
    );
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


/**
 * Read a value from a flag (`--name`/`--name=value`). Returns true if
 * consumed; mutates ``copy`` in place.
 *
 * @param {string[]} copy
 * @param {string} flag
 * @param {Record<string, string|null>} out
 * @param {string} key
 */
export function consumeStringFlag(copy, flag, out, key) {
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
}


/**
 * Read a body / file argument. ``--body <inline>`` takes the next arg
 * as the literal body; ``--body-file <path>`` reads the file content.
 * Mutually exclusive — the second wins if both are given but we warn.
 *
 * @param {string[]} copy
 * @param {Record<string, string|null>} out
 */
export function consumeBodyFlags(copy, out) {
  if (consumeStringFlag(copy, "--body", out, "body")) return true;
  if (consumeStringFlag(copy, "--body-file", out, "bodyFile")) return true;
  return false;
}


/**
 * Resolve ``--body`` vs ``--body-file`` into a single string. Reads
 * stdin when ``--body-file=-`` so callers can pipe markdown.
 *
 * @param {{ body?: string|null, bodyFile?: string|null }} out
 */
export async function readBodyFromOpts(out) {
  if (out.body && out.bodyFile) {
    console.error("Pass --body OR --body-file, not both.");
    process.exit(1);
  }
  if (out.bodyFile) {
    const fs = await import("node:fs/promises");
    if (out.bodyFile === "-") {
      const chunks = [];
      for await (const chunk of process.stdin) chunks.push(chunk);
      return Buffer.concat(chunks).toString("utf8");
    }
    return await fs.readFile(out.bodyFile, "utf8");
  }
  return out.body || "";
}
