import crypto from "node:crypto";
import { getUserAgent } from "./version.mjs";

function joinUrl(baseUrl, path) {
  return `${baseUrl.replace(/\/$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
}

function authHeaders() {
  const token = process.env.SHIP_API_TOKEN;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/* Stamp a real User-Agent on every outbound call so the methodology API can
 * correlate adoption metrics with the CLI release. The version string lives
 * in cli/package.json (kept in sync with the root VERSION file). */
function commonHeaders() {
  return {
    "User-Agent": getUserAgent(),
    ...authHeaders(),
  };
}

export class HttpError extends Error {
  constructor(status, statusText, url, body) {
    const msg = typeof body === "string" ? body : JSON.stringify(body);
    super(`HTTP ${status} ${statusText} for ${url}\n${msg}`);
    this.status = status;
    this.statusText = statusText;
    this.url = url;
    this.body = body;
  }
}

/**
 * @param {string} baseUrl
 * @param {string} path
 * @param {Record<string, unknown>} body
 */
export async function apiPost(baseUrl, path, body) {
  const url = joinUrl(baseUrl, path);
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...commonHeaders(),
    },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) throw new HttpError(res.status, res.statusText, url, data ?? text);
  return data;
}

/**
 * @param {string} baseUrl
 * @param {string} path
 */
export async function apiGet(baseUrl, path) {
  const url = joinUrl(baseUrl, path);
  const res = await fetch(url, {
    headers: { Accept: "application/json", ...commonHeaders() },
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) throw new HttpError(res.status, res.statusText, url, data ?? text);
  return data;
}

/**
 * Aggregated catalog. RFC-0005 removed the legacy `/manifest` endpoint
 * and RFC-0007 Phase 6 retired the ``artifact_kind=workflow`` layer;
 * the catalog is now exposed as three per-kind routes (`/patterns`,
 * `/tools`, `/collections`). This helper fans them out in parallel and
 * stamps a `kind` field on each entry so callers (sync, verify) keep
 * their existing single-list shape. `channel` is applied client-side
 * because the per-kind endpoints don't filter today.
 *
 * @param {string} baseUrl
 * @param {{channel?:string}} [opts]
 * @returns {Promise<Array<object>>}
 */
export async function fetchManifest(baseUrl, { channel } = {}) {
  const KINDS = [
    { plural: "collections", singular: "collection" },
  ];
  const responses = await Promise.all(
    KINDS.map((k) => apiGet(baseUrl, `/${k.plural}`)),
  );
  /** @type {Array<object>} */
  const entries = [];
  for (let i = 0; i < KINDS.length; i += 1) {
    const { plural, singular } = KINDS[i];
    const data = responses[i];
    const arr = data && Array.isArray(data[plural]) ? data[plural] : [];
    for (const e of arr) {
      entries.push({ ...e, kind: e.kind || singular });
    }
  }
  const wantChannel = (channel || "").toLowerCase();
  if (wantChannel && wantChannel !== "edge") {
    return entries.filter(
      (e) => (e.channel || "stable").toLowerCase() === wantChannel,
    );
  }
  return entries;
}

function sha256Hex(buf) {
  return crypto.createHash("sha256").update(buf).digest("hex");
}

/**
 * POST /fetch with {kind,id,version?}. Handles 404 (version_not_found) and 410 (yanked).
 * Returns {content, meta} where meta is the manifest record + source_url.
 * @param {string} baseUrl
 * @param {string} kind
 * @param {string} id
 * @param {string} [version]
 */
export async function fetchArtifact(baseUrl, kind, id, version) {
  const body = { kind, id };
  if (version) body.version = version;
  let data;
  try {
    data = await apiPost(baseUrl, "/fetch", body);
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) {
      const err = new Error(
        `artifact not found: ${kind}:${id}${version ? `@${version}` : ""}`,
      );
      err.code = "ARTIFACT_NOT_FOUND";
      throw err;
    }
    if (e instanceof HttpError && e.status === 410) {
      const err = new Error(
        `artifact yanked: ${kind}:${id}${version ? `@${version}` : ""}`,
      );
      err.code = "ARTIFACT_YANKED";
      throw err;
    }
    throw e;
  }
  if (!data || typeof data.content !== "string") {
    throw new Error(`POST /fetch: missing 'content' in response for ${kind}:${id}`);
  }
  const content = data.content;
  const meta = {
    kind: data.kind || kind,
    id: data.id || id,
    version: data.version || version || null,
    content_sha256: data.content_sha256 || sha256Hex(Buffer.from(content, "utf8")),
    updated_at: data.updated_at || null,
    channel: data.channel || null,
    source_url: `${baseUrl.replace(/\/$/, "")}/fetch`,
  };
  return { content, meta };
}

/**
 * POST /fetch with {path, version?} — for raw doc paths.
 * @param {string} baseUrl
 * @param {string} repoRelativePath
 * @param {string} [version]
 */
export async function fetchDoc(baseUrl, repoRelativePath, version) {
  const body = { path: repoRelativePath };
  if (version) body.version = version;
  let data;
  try {
    data = await apiPost(baseUrl, "/fetch", body);
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) {
      const err = new Error(`doc not found: ${repoRelativePath}`);
      err.code = "ARTIFACT_NOT_FOUND";
      throw err;
    }
    if (e instanceof HttpError && e.status === 410) {
      const err = new Error(`doc yanked: ${repoRelativePath}`);
      err.code = "ARTIFACT_YANKED";
      throw err;
    }
    throw e;
  }
  if (!data || typeof data.content !== "string") {
    throw new Error(`POST /fetch: missing 'content' in response for ${repoRelativePath}`);
  }
  const content = data.content;
  const meta = {
    kind: "doc",
    id: data.id || repoRelativePath,
    path: repoRelativePath,
    version: data.version || version || null,
    content_sha256: data.content_sha256 || sha256Hex(Buffer.from(content, "utf8")),
    updated_at: data.updated_at || null,
    source_url: `${baseUrl.replace(/\/$/, "")}/fetch`,
  };
  return { content, meta };
}

/**
 * Best-effort POST /telemetry. Silent on network errors (buffer in outbox).
 * Returns {ok, status?, error?}.
 */
export async function postTelemetry(baseUrl, events) {
  try {
    const data = await apiPost(baseUrl, "/telemetry", { events });
    return { ok: true, data };
  } catch (e) {
    return { ok: false, error: e };
  }
}

/**
 * GET /telemetry/<anonymous_id>/export → { events: [...] }.
 * @param {string} baseUrl
 * @param {string} anonymousId
 */
export async function exportTelemetry(baseUrl, anonymousId) {
  return apiGet(baseUrl, `/telemetry/${encodeURIComponent(anonymousId)}/export`);
}

/**
 * DELETE /telemetry/<anonymous_id> with X-Ship-Confirm: yes.
 * Returns the server JSON (expected shape: { deleted: N }).
 * @param {string} baseUrl
 * @param {string} anonymousId
 */
export async function deleteTelemetry(baseUrl, anonymousId) {
  const url = joinUrl(baseUrl, `/telemetry/${encodeURIComponent(anonymousId)}`);
  const res = await fetch(url, {
    method: "DELETE",
    headers: {
      Accept: "application/json",
      "X-Ship-Confirm": "yes",
      ...commonHeaders(),
    },
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) throw new HttpError(res.status, res.statusText, url, data ?? text);
  return data;
}

/**
 * POST /feedback → { issue_url, deduplicated, ... }.
 * Server errors are thrown verbatim via HttpError so callers can surface the
 * server-provided message.
 * @param {string} baseUrl
 * @param {object} body
 */
export async function postFeedback(baseUrl, body) {
  return apiPost(baseUrl, "/feedback", body);
}
