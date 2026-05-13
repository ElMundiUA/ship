#!/usr/bin/env node
/**
 * bunny-fetch-logs — pull container stdout/stderr from Bunny Magic Containers.
 *
 * Same auth + base URL shape as `bunny-deploy-platform.mjs`. Bunny exposes a
 * paginated logs endpoint per app (works for the whole app or a specific
 * container template). When the deployed backend is hitting Bunny's
 * "We're deploying your app!" placeholder for too long, the answer is
 * almost always in the container logs (crash loop, missing migration,
 * env var typo).
 *
 * Usage:
 *
 *   BUNNY_ACCESS_KEY=...  BUNNY_BACKEND_APP_ID=...  \
 *     node scripts/bunny-fetch-logs.mjs
 *
 *   # console app instead of backend:
 *   BUNNY_ACCESS_KEY=...  BUNNY_CONSOLE_APP_ID=...  \
 *     node scripts/bunny-fetch-logs.mjs --app console
 *
 *   # last 200 lines, follow mode (re-fetch every 5s):
 *   node scripts/bunny-fetch-logs.mjs --tail 200 --follow
 *
 *   # narrow to a specific template (see `bunny-deploy-platform.mjs`
 *   # for default names — ship-server, console, …):
 *   node scripts/bunny-fetch-logs.mjs --template ship-server
 *
 * Env contract (set inline or in `.env` next to the deploy creds):
 *
 *   BUNNY_ACCESS_KEY              account API key with Magic Containers read
 *   BUNNY_BACKEND_APP_ID          MC app id for ship-server / ship-worker
 *   BUNNY_CONSOLE_APP_ID          MC app id for ship-console (use --app console)
 *   BUNNY_BACKEND_CONTAINER       template name override (default: ship-server)
 *   BUNNY_CONSOLE_CONTAINER       template name override (default: console)
 */

const BASE = "https://api.bunny.net/mc";

function need(name) {
  const v = (process.env[name] || "").trim();
  if (!v) throw new Error(`Set ${name}`);
  return v;
}

function arg(flag, fallback) {
  const idx = process.argv.indexOf(flag);
  if (idx === -1) return fallback;
  const value = process.argv[idx + 1];
  if (!value || value.startsWith("--")) return true;
  return value;
}

async function api(path, { key, method = "GET" } = {}) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { AccessKey: key, accept: "application/json" },
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`${method} ${path} ${res.status}: ${text}`);
  }
  return text ? JSON.parse(text) : null;
}

async function findTemplate(app, containerName) {
  const templates = app.containerTemplates || [];
  if (!containerName) {
    // No name asked for — return the only template if there's one,
    // otherwise list them and bail.
    if (templates.length === 1) return templates[0];
    const known = templates.map((x) => x.name).join(", ") || "(none)";
    throw new Error(
      `multiple templates in app "${app.name}" (${known}); pick one with --template`,
    );
  }
  const t = templates.find((x) => x.name === containerName);
  if (!t) {
    const known = templates.map((x) => x.name).join(", ") || "(none)";
    throw new Error(
      `no template "${containerName}" in app "${app.name}". known: ${known}`,
    );
  }
  return t;
}

async function fetchLogs({ key, appId, templateId, tail }) {
  // Bunny's MC logs endpoint is paginated; we ask for the most recent page
  // and slice client-side. Shape: { items: [{ timestamp, message, source, ...}] }.
  const qs = new URLSearchParams({ pageSize: String(tail) });
  if (templateId) qs.set("containerTemplateId", templateId);
  const path = `/apps/${encodeURIComponent(appId)}/logs?${qs.toString()}`;
  const data = await api(path, { key });
  // Bunny pagination wrappers vary by endpoint version — accept either
  // `{items:[]}` or a bare array.
  const rows = Array.isArray(data) ? data : data?.items ?? data?.logs ?? [];
  return rows;
}

function fmtRow(row) {
  const ts = row.timestamp || row.time || row.createdAt || "";
  const src = row.source || row.stream || row.containerName || "";
  const msg = row.message || row.text || row.line || "";
  return `${ts} [${src}] ${msg}`.trim();
}

async function main() {
  const key = need("BUNNY_ACCESS_KEY");
  const which = arg("--app", "backend");
  const tail = Number(arg("--tail", "100"));
  const follow = Boolean(arg("--follow", false));
  const explicitTemplate = arg("--template", "");

  const appId = need(which === "console" ? "BUNNY_CONSOLE_APP_ID" : "BUNNY_BACKEND_APP_ID");
  const defaultContainerEnv =
    which === "console" ? "BUNNY_CONSOLE_CONTAINER" : "BUNNY_BACKEND_CONTAINER";
  const fallbackTemplate =
    which === "console" ? "console" : "ship-server";
  const templateName =
    typeof explicitTemplate === "string" && explicitTemplate
      ? explicitTemplate
      : (process.env[defaultContainerEnv] || fallbackTemplate);

  const app = await api(`/apps/${encodeURIComponent(appId)}`, { key });
  let template = null;
  try {
    template = await findTemplate(app, templateName);
  } catch (err) {
    // If the named template isn't there, fall back to "no filter" — Bunny
    // returns whole-app logs in that case.
    console.error(`[warn] ${err.message}; fetching app-wide logs.`);
  }

  let lastSeen = "";
  while (true) {
    const rows = await fetchLogs({
      key,
      appId,
      templateId: template?.id,
      tail,
    });
    for (const row of rows) {
      const line = fmtRow(row);
      if (!line || line === lastSeen) continue;
      lastSeen = line;
      console.log(line);
    }
    if (!follow) break;
    await new Promise((r) => setTimeout(r, 5000));
  }
}

main().catch((err) => {
  console.error(err.message || String(err));
  process.exit(1);
});
