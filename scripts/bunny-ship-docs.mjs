#!/usr/bin/env node
/**
 * Ship docs — Bunny Magic Containers (official API: https://api.bunny.net/mc)
 *
 * Subcommand: ensure — create app "Ship docs" + container if missing, deploy, print ids/host.
 * Env:
 *   BUNNY_ACCESS_KEY (required) — account API key with Magic Containers
 *   BUNNY_APP_ID (optional) — if set, skip create and use this id
 *   SHIP_MC_APP_NAME (optional) — default "ship-docs"
 *   DOCKER_IMAGE_NAME (optional) — default "dekus/ship-docs" (namespace/name)
 *   MC_CONTAINER_NAME (optional) — default "Container-1" (Bunny UI default); script also resolves real name via GET /apps/{id}
 *   IMAGE_TAG (optional) — default "latest"
 */
import { appendFileSync } from "fs";

const BASE = "https://api.bunny.net/mc";

function out(name, value) {
  const v = String(value ?? "").replace(/\r?\n/g, " ");
  const gh = process.env.GITHUB_OUTPUT;
  if (gh) appendFileSync(gh, `${name}=${v}\n`);
  console.log(`${name}=${v}`);
}

function parseImage(full) {
  const s = full.trim();
  const i = s.lastIndexOf("/");
  if (i <= 0) throw new Error(`DOCKER_IMAGE_NAME must be namespace/name, got: ${full}`);
  return { namespace: s.slice(0, i), name: s.slice(i + 1) };
}

async function mcFetch(path, { method = "GET", key, body } = {}) {
  const url = path.startsWith("http") ? path : `${BASE}${path}`;
  const r = await fetch(url, {
    method,
    headers: {
      AccessKey: key,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    /* leave json null */
  }
  return { ok: r.ok, status: r.status, text, json };
}

/** Resolve template name for BunnyWay/actions/container-update-image (GET app — dashboard apps often use "Container-1"). */
async function resolveMcContainerName(key, appId, preferred) {
  const { ok, status, json, text } = await mcFetch(`/apps/${encodeURIComponent(appId)}`, { key });
  if (!ok) {
    console.warn(`GET /apps/${appId} failed ${status}: ${text} — using MC_CONTAINER_NAME=${preferred}`);
    return preferred;
  }
  const templates = json?.containerTemplates;
  if (!Array.isArray(templates) || templates.length === 0) {
    console.warn("No containerTemplates on app — using MC_CONTAINER_NAME");
    return preferred;
  }
  const exact = templates.find((t) => t?.name === preferred);
  if (exact) return String(exact.name);
  if (templates.length === 1) return String(templates[0].name);
  console.warn(
    `Multiple container templates (${templates.length}); none named "${preferred}"; using first: ${templates[0].name}`
  );
  return String(templates[0].name);
}

async function listAllApps(key) {
  const all = [];
  let cursor = null;
  for (let n = 0; n < 50; n++) {
    const q = new URLSearchParams({ limit: "100" });
    if (cursor) q.set("nextCursor", cursor);
    const { ok, status, json, text } = await mcFetch(`/apps?${q.toString()}`, { key });
    if (!ok) throw new Error(`List apps failed ${status}: ${text}`);
    const items = json?.items ?? json?.pageItems ?? [];
    all.push(...items);
    cursor = json?.cursor ?? null;
    if (!cursor) break;
  }
  return all;
}

function probe(path) {
  return {
    initialDelaySeconds: 5,
    periodSeconds: 15,
    timeoutSeconds: 5,
    failureThreshold: 5,
    httpGet: {
      request: { path, portNumber: 8080 },
      response: { expectedStatusCode: "ok" },
    },
  };
}

/** Strict minimal template — no registry config-suggestions (those payloads still 500’d on Bunny). */
function minimalContainerTemplate({ namespace, imageName, tag, containerName, includeProbes }) {
  const t = {
    name: containerName,
    imageName,
    imageNamespace: namespace,
    imageRegistryId: "dockerhub",
    imageTag: tag,
    imagePullPolicy: "always",
    endpoints: [
      {
        displayName: "web",
        cdn: {
          isSslEnabled: true,
          portMappings: [{ containerPort: 8080, protocols: ["tcp"] }],
        },
      },
    ],
  };
  if (includeProbes) {
    t.probes = {
      startup: { ...probe("/health"), initialDelaySeconds: 3, failureThreshold: 10 },
      readiness: probe("/health"),
      liveness: probe("/health"),
    };
  }
  return t;
}

async function postCreateApp(key, body, withRetries) {
  let created = await mcFetch("/apps", { method: "POST", key, body });
  if (!withRetries) return created;
  for (let attempt = 1; !created.ok && created.status >= 500 && attempt < 4; attempt++) {
    await new Promise((r) => setTimeout(r, 5000 * attempt));
    created = await mcFetch("/apps", { method: "POST", key, body });
  }
  return created;
}

async function ensure() {
  const key =
    process.env.BUNNY_ACCESS_KEY?.trim() ||
    process.env.BUNNY_ACCESS_KEY_FALLBACK?.trim();
  if (!key) throw new Error("Set BUNNY_ACCESS_KEY or BUNNY_ACCESS_KEY_FALLBACK (Bunny account API key)");

  // ASCII name without spaces — Bunny Create app rejected some titles with 500.
  const appName = (process.env.SHIP_MC_APP_NAME || "ship-docs").trim();
  const imageFull = (process.env.DOCKER_IMAGE_NAME || "dekus/ship-docs").trim();
  const { namespace, name: imageBase } = parseImage(imageFull);
  const containerName = (process.env.MC_CONTAINER_NAME || "Container-1").trim();
  const tag = (process.env.IMAGE_TAG || "latest").trim();

  let appId = (process.env.BUNNY_APP_ID || "").trim();

  const apps = await listAllApps(key);
  // Explicit BUNNY_APP_ID (e.g. repo variable) wins — do not overwrite with name match.
  if (!appId) {
    const byName = apps.find((a) => a?.name === appName);
    if (byName) appId = String(byName.id);
  }

  if (!appId) {
    const regions = (process.env.BUNNY_REGION_IDS || "DE,UK,US")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const primary = regions.length ? regions : ["DE", "UK", "US"];

    function buildCreateBody(allowedRegionIds, includeProbes) {
      return {
        name: appName,
        runtimeType: "shared",
        autoScaling: { min: 1, max: 1 },
        regionSettings: allowedRegionIds.length ? { allowedRegionIds } : {},
        containerTemplates: [
          minimalContainerTemplate({
            namespace,
            imageName: imageBase,
            tag,
            containerName,
            includeProbes,
          }),
        ],
      };
    }

    const variantKeys = [];
    const variants = [];
    const add = (allowedRegionIds, includeProbes) => {
      const k = `${JSON.stringify(allowedRegionIds)}|${includeProbes}`;
      if (variantKeys.includes(k)) return;
      variantKeys.push(k);
      variants.push(buildCreateBody(allowedRegionIds, includeProbes));
    };

    add(primary, true);
    if (JSON.stringify(primary) !== JSON.stringify(["DE"])) add(["DE"], true);
    add([], true);
    add(primary, false);
    if (JSON.stringify(primary) !== JSON.stringify(["DE"])) add(["DE"], false);
    add([], false);

    let created = { ok: false, status: 0, text: "no attempts" };
    for (let vi = 0; vi < variants.length; vi++) {
      const body = variants[vi];
      created = await postCreateApp(key, body, vi === 0);
      if (created.ok) break;
      if (created.status !== 500) {
        throw new Error(`Create app ${created.status}: ${created.text}`);
      }
    }
    if (!created.ok) {
      const hint =
        created.status === 500
          ? " Bunny often returns 500 here for account/billing/API issues (not invalid JSON). " +
            "Workaround: create a Magic Container app named \"" +
            appName +
            "\" in the Bunny dashboard (or set repo variable BUNNY_APP_ID), then re-run this workflow."
          : "";
      throw new Error(`Create app ${created.status}: ${created.text}${hint}`);
    }
    appId = String(created.json?.id || "");
    if (!appId) throw new Error(`Create app: no id in response: ${created.text}`);

    const dep = await mcFetch(`/apps/${appId}/deploy`, { method: "POST", key });
    if (!dep.ok) throw new Error(`Deploy ${dep.status}: ${dep.text}`);
  }

  out("app_id", appId);

  const resolvedContainer = await resolveMcContainerName(key, appId, containerName);
  out("mc_container_name", resolvedContainer);

  let host = "";
  for (let i = 0; i < 24; i++) {
    const fresh = await listAllApps(key);
    const row = fresh.find((a) => String(a.id) === String(appId));
    host =
      row?.displayEndpoint?.address ||
      (row?.displayEndpoint?.uri || "").replace(/^https?:\/\//, "").replace(/\/$/, "") ||
      "";
    if (host) break;
    await new Promise((r) => setTimeout(r, 5000));
  }
  out("bunny_default_host", host);
}

const cmd = process.argv[2] || "ensure";
if (cmd === "ensure") {
  await ensure();
} else {
  console.error("Usage: node scripts/bunny-ship-docs.mjs ensure");
  process.exit(1);
}
