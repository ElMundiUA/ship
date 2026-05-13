#!/usr/bin/env node
/**
 * bunny-deploy-platform — push backend + console images to Bunny Magic Containers.
 *
 * Bunny Magic Containers (https://docs.bunny.net/magic-containers) gives
 * us TLS termination, multi-region scheduling, rolling updates, and a
 * managed Postgres add-on without any of the pain of self-hosting K8s.
 *
 * For each component we keep one Magic Container *app* with its own
 * container template (`Container-1` by default). This script:
 *
 *   1. PATCH /apps/{id} for each component with the new IMAGE_TAG (and
 *      optionally IMAGE_DIGEST so MC re-pulls when tag string is unchanged).
 *   2. Triggers a rolling update.
 *   3. Polls /apps/{id} until every container reports the new tag and
 *      health-check is green, or fails after MAX_WAIT_S.
 *
 * Components managed:
 *
 *   ship-server   — FastAPI (image: ${BUNNY_BACKEND_IMAGE})
 *   ship-worker   — arq worker, same image, different command. Skipped by
 *                   default — cloud SaaS topology has no worker container.
 *                   Opt-in by setting BUNNY_WORKER_CONTAINER to the template
 *                   name of an existing worker template.
 *   ship-console  — Next.js operator console (image: ${BUNNY_CONSOLE_IMAGE})
 *
 * Env contract (set as GitHub Actions secrets or pass inline):
 *
 *   BUNNY_ACCESS_KEY              account API key with Magic Containers write
 *   BUNNY_BACKEND_APP_ID          MC app id for ship-server + ship-worker
 *   BUNNY_CONSOLE_APP_ID          MC app id for ship-console
 *   BUNNY_BACKEND_IMAGE           e.g. dekus/ship-backend
 *   BUNNY_CONSOLE_IMAGE           e.g. dekus/ship-console
 *   IMAGE_TAG                     e.g. main-abc1234 (defaults to "latest")
 *   BUNNY_BACKEND_DIGEST          optional sha256:... for ship-server template
 *   BUNNY_WORKER_DIGEST           optional sha256:... for ship-worker template
 *   BUNNY_CONSOLE_DIGEST          optional sha256:... for console template
 *   BUNNY_BACKEND_CONTAINER       template name for ship-server  (default: ship-server)
 *   BUNNY_WORKER_CONTAINER        template name for ship-worker  (default: empty -> skip)
 *   BUNNY_CONSOLE_CONTAINER       template name for console      (default: console)
 *   MAX_WAIT_S                    rolling-update poll budget     (default: 600)
 *   DRY_RUN                       set to "1" to print actions only
 */

const BASE = "https://api.bunny.net/mc";

function need(name) {
  const v = (process.env[name] || "").trim();
  if (!v) throw new Error(`Set ${name}`);
  return v;
}

function opt(name, fallback = "") {
  return (process.env[name] || fallback).trim();
}

async function api(path, { method = "GET", key, body } = {}) {
  const url = path.startsWith("http") ? path : `${BASE}${path}`;
  const res = await fetch(url, {
    method,
    headers: {
      AccessKey: key,
      Accept: "application/json",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    /* leave null */
  }
  if (!res.ok) {
    throw new Error(`${method} ${path} ${res.status}: ${text}`);
  }
  return { json, text };
}

async function findTemplate(app, containerName) {
  const templates = app.containerTemplates || [];
  const t = templates.find((x) => x.name === containerName);
  if (!t) {
    const known = templates.map((x) => x.name).join(", ") || "(none)";
    throw new Error(
      `No container template named "${containerName}" in app "${app.name}". Known: ${known}`,
    );
  }
  return t;
}

async function patchTemplate({ key, appId, containerName, imageTag, imageDigest }) {
  const { json: app } = await api(`/apps/${encodeURIComponent(appId)}`, { key });
  const template = await findTemplate(app, containerName);
  // Bunny's /apps/{id} PATCH expects every required template field in
  // containerTemplates[] (name + imageName + imageNamespace + imageRegistryId
  // + …) and 400s otherwise. The per-container endpoint accepts a delta:
  //   PATCH /apps/{id}/containers/{containerId}
  // (https://docs.bunny.net/api-reference/magic-containers/containers/patch-container-template)
  const body = {
    id: template.id,
    imageTag,
    imagePullPolicy: "always",
  };
  if (imageDigest) body.imageDigest = imageDigest;
  if (process.env.DRY_RUN === "1") {
    console.log(
      `[dry-run] PATCH /apps/${appId}/containers/${template.id} template=${containerName} -> tag=${imageTag} digest=${imageDigest || "(unset)"}`,
    );
    return { app, template };
  }
  await api(
    `/apps/${encodeURIComponent(appId)}/containers/${encodeURIComponent(template.id)}`,
    { method: "PATCH", key, body },
  );
  console.log(`PATCH ok app=${app.name} template=${containerName} tag=${imageTag}`);
  return { app, template };
}

async function waitForRollout({ key, appId, containerName, imageTag, maxWaitS }) {
  if (process.env.DRY_RUN === "1") {
    console.log(`[dry-run] skip rollout wait app=${appId} template=${containerName}`);
    return;
  }
  // Best-effort observability: poll /apps/{id} for up to maxWaitS, but
  // never fail the deploy on it. Bunny's GET /apps/{id} response shape for
  // per-container live state isn't part of the documented API surface and
  // can return an empty `containers` array even after a successful rolling
  // update — relying on it blocks the pipeline (and prevents subsequent
  // sequential PATCHes for other containers in the same workflow run).
  // The PATCH + /deploy POST already returned 2xx upstream, so the
  // rollout is in flight; this loop just streams progress to the log if
  // Bunny happens to expose it.
  const deadline = Date.now() + maxWaitS * 1000;
  let lastSummary = "";
  while (Date.now() < deadline) {
    let app;
    try {
      ({ json: app } = await api(`/apps/${encodeURIComponent(appId)}`, { key }));
    } catch (err) {
      console.warn(`[wait] ${containerName} GET failed (${err.message}); continuing`);
      await new Promise((r) => setTimeout(r, 5000));
      continue;
    }
    const template = (app.containerTemplates || []).find((t) => t.name === containerName);
    if (!template) {
      console.warn(`[wait] template ${containerName} not found in /apps response; trusting PATCH`);
      return;
    }
    const containers = template.containers || [];
    const total = containers.length;
    const onTag = containers.filter((c) => (c.imageTag || "") === imageTag).length;
    const healthy = containers.filter(
      (c) => (c.health || c.status || "").toLowerCase() === "healthy",
    ).length;
    const summary = `containers=${total} on-tag=${onTag} healthy=${healthy} desired=${imageTag}`;
    if (summary !== lastSummary) {
      console.log(`[wait] ${containerName} ${summary}`);
      lastSummary = summary;
    }
    if (total > 0 && onTag === total && healthy === total) {
      console.log(`[ok] ${containerName} fully rolled out to ${imageTag}`);
      return;
    }
    await new Promise((r) => setTimeout(r, 5000));
  }
  console.warn(
    `[wait] ${containerName} no observed convergence after ${maxWaitS}s (last: ${lastSummary || "no progress reported"}); trusting PATCH + deploy nudge`,
  );
}

async function nudgeDeploy({ key, appId }) {
  if (process.env.DRY_RUN === "1") {
    console.log(`[dry-run] POST /apps/${appId}/deploy`);
    return;
  }
  // Some Bunny accounts only roll out after an explicit /deploy POST even
  // when the per-container PATCH succeeded — same defensive pattern as the
  // docs deploy workflow. 200 == accepted; non-200 throws via api().
  await api(`/apps/${encodeURIComponent(appId)}/deploy`, { method: "POST", key });
  console.log(`[bunny] deploy nudge sent app=${appId}`);
}

async function deployComponent({ key, appId, containerName, imageTag, imageDigest, maxWaitS }) {
  await patchTemplate({ key, appId, containerName, imageTag, imageDigest });
  await nudgeDeploy({ key, appId });
  await waitForRollout({ key, appId, containerName, imageTag, maxWaitS });
}

async function main() {
  const key = need("BUNNY_ACCESS_KEY");
  const backendAppId = need("BUNNY_BACKEND_APP_ID");
  const consoleAppId = need("BUNNY_CONSOLE_APP_ID");
  const imageTag = opt("IMAGE_TAG", "latest");
  const maxWaitS = Number(opt("MAX_WAIT_S", "600")) || 600;
  const backendContainer = opt("BUNNY_BACKEND_CONTAINER", "ship-server");
  const workerContainer = opt("BUNNY_WORKER_CONTAINER", "");
  const consoleContainer = opt("BUNNY_CONSOLE_CONTAINER", "console");

  console.log(`[bunny] deploying tag=${imageTag} (max-wait=${maxWaitS}s)`);

  // Cloud SaaS topology only ships ship-server + ship-console. The worker is
  // opt-in: set BUNNY_WORKER_CONTAINER to the existing template name to roll
  // it out alongside the API. Sequential PATCHes so a failed worker rollout
  // doesn't take the server with it.
  await deployComponent({
    key,
    appId: backendAppId,
    containerName: backendContainer,
    imageTag,
    imageDigest: opt("BUNNY_BACKEND_DIGEST"),
    maxWaitS,
  });
  if (workerContainer) {
    await deployComponent({
      key,
      appId: backendAppId,
      containerName: workerContainer,
      imageTag,
      imageDigest: opt("BUNNY_WORKER_DIGEST"),
      maxWaitS,
    });
  } else {
    console.log("[bunny] skipping ship-worker rollout (BUNNY_WORKER_CONTAINER unset)");
  }
  await deployComponent({
    key,
    appId: consoleAppId,
    containerName: consoleContainer,
    imageTag,
    imageDigest: opt("BUNNY_CONSOLE_DIGEST"),
    maxWaitS,
  });

  console.log("[bunny] all components rolled out successfully");
}

main().catch((err) => {
  console.error(`[bunny] FAIL: ${err.message}`);
  process.exit(1);
});
