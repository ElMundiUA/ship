#!/usr/bin/env node
/**
 * Ship docs — Bunny Magic Containers (official API: https://api.bunny.net/mc)
 *
 * Subcommand: ensure — create app "Ship docs" + container if missing, deploy, print ids/host.
 * Env:
 *   BUNNY_ACCESS_KEY (required) — account API key with Magic Containers
 *   BUNNY_APP_ID (optional) — if set, skip create and use this id
 *   SHIP_MC_APP_NAME (optional) — default "Ship docs"
 *   DOCKER_IMAGE_NAME (optional) — default "dekus/ship-docs" (namespace/name)
 *   MC_CONTAINER_NAME (optional) — default "ship" (must match BunnyWay action `container`)
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

function buildContainerTemplate({ namespace, imageName, tag, containerName, suggestions }) {
  let endpoints = suggestions?.endpointSuggestions;
  if (!Array.isArray(endpoints) || endpoints.length === 0) {
    endpoints = [
      {
        displayName: "web",
        cdn: {
          isSslEnabled: true,
          portMappings: [{ containerPort: 8080, protocols: ["tcp"] }],
        },
      },
    ];
  } else {
    endpoints = structuredClone(endpoints);
    for (const ep of endpoints) {
      if (ep.cdn?.portMappings?.length) {
        ep.cdn.portMappings = ep.cdn.portMappings.map((pm) => ({
          ...pm,
          containerPort: 8080,
        }));
      }
    }
  }

  return {
    name: containerName,
    imageName,
    imageNamespace: namespace,
    imageRegistryId: "dockerhub",
    imageTag: tag,
    imagePullPolicy: "always",
    endpoints,
    probes: {
      startup: { ...probe("/health"), initialDelaySeconds: 3, failureThreshold: 10 },
      readiness: probe("/health"),
      liveness: probe("/health"),
    },
  };
}

async function ensure() {
  const key =
    process.env.BUNNY_ACCESS_KEY?.trim() ||
    process.env.BUNNY_ACCESS_KEY_FALLBACK?.trim();
  if (!key) throw new Error("Set BUNNY_ACCESS_KEY or BUNNY_ACCESS_KEY_FALLBACK (Bunny account API key)");

  const appName = (process.env.SHIP_MC_APP_NAME || "Ship docs").trim();
  const imageFull = (process.env.DOCKER_IMAGE_NAME || "dekus/ship-docs").trim();
  const { namespace, name: imageBase } = parseImage(imageFull);
  const containerName = (process.env.MC_CONTAINER_NAME || "ship").trim();
  const tag = (process.env.IMAGE_TAG || "latest").trim();

  let appId = (process.env.BUNNY_APP_ID || "").trim();

  const apps = await listAllApps(key);
  const byName = apps.find((a) => a?.name === appName);
  if (byName) appId = String(byName.id);

  if (!appId) {
    const sug = await mcFetch("/registries/config-suggestions", {
      method: "POST",
      key,
      body: {
        registryId: "dockerhub",
        imageNamespace: namespace,
        imageName: imageBase,
        tag,
      },
    });
    if (!sug.ok) throw new Error(`config-suggestions ${sug.status}: ${sug.text}`);

    const body = {
      name: appName,
      runtimeType: "shared",
      autoScaling: { min: 1, max: 1 },
      regionSettings: {},
      containerTemplates: [
        buildContainerTemplate({
          namespace,
          imageName: imageBase,
          tag,
          containerName,
          suggestions: sug.json,
        }),
      ],
    };

    const created = await mcFetch("/apps", { method: "POST", key, body });
    if (!created.ok) throw new Error(`Create app ${created.status}: ${created.text}`);
    appId = String(created.json?.id || "");
    if (!appId) throw new Error(`Create app: no id in response: ${created.text}`);

    const dep = await mcFetch(`/apps/${appId}/deploy`, { method: "POST", key });
    if (!dep.ok) throw new Error(`Deploy ${dep.status}: ${dep.text}`);
  }

  out("app_id", appId);

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
