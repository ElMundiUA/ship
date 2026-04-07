#!/usr/bin/env node
/**
 * PATCH Magic Container template with image tag (+ optional digest). Awaits Bunny API response.
 * Bunny rolling updates start from this PATCH (see https://docs.bunny.net/magic-containers/rolling-updates).
 *
 * Env:
 *   BUNNY_ACCESS_KEY (required)
 *   BUNNY_APP_ID (required)
 *   MC_CONTAINER_NAME (required) — template name, e.g. Container-1
 *   IMAGE_TAG (required)
 *   IMAGE_DIGEST (optional) — sha256:... from docker/build-push-action digest output (recommended)
 */
const BASE = "https://api.bunny.net/mc";

async function main() {
  const key =
    process.env.BUNNY_ACCESS_KEY?.trim() || process.env.BUNNY_ACCESS_KEY_FALLBACK?.trim();
  const appId = process.env.BUNNY_APP_ID?.trim();
  const containerName = process.env.MC_CONTAINER_NAME?.trim();
  const imageTag = process.env.IMAGE_TAG?.trim();
  const imageDigest = (process.env.IMAGE_DIGEST || "").trim();

  if (!key) throw new Error("Set BUNNY_ACCESS_KEY (or BUNNY_ACCESS_KEY_FALLBACK)");
  if (!appId) throw new Error("Set BUNNY_APP_ID");
  if (!containerName) throw new Error("Set MC_CONTAINER_NAME");
  if (!imageTag) throw new Error("Set IMAGE_TAG");

  const getRes = await fetch(`${BASE}/apps/${encodeURIComponent(appId)}`, {
    headers: { AccessKey: key, Accept: "application/json" },
  });
  const getText = await getRes.text();
  if (!getRes.ok) throw new Error(`GET /apps/${appId} ${getRes.status}: ${getText}`);

  const app = JSON.parse(getText);
  const templates = app.containerTemplates || [];
  const t = templates.find((x) => x.name === containerName);
  if (!t) {
    const names = templates.map((x) => x.name).join(", ") || "(none)";
    throw new Error(`No container template named "${containerName}". Known: ${names}`);
  }

  const body = {
    id: t.id,
    imageTag,
    // Ensure MC re-pulls when tag string is unchanged but registry manifest moved
    imagePullPolicy: "always",
  };
  if (imageDigest) body.imageDigest = imageDigest;
  else console.warn("IMAGE_DIGEST empty — PATCH is tag-only; Bunny may show “update available” until digest matches registry.");

  const patchRes = await fetch(
    `${BASE}/apps/${encodeURIComponent(appId)}/containers/${encodeURIComponent(t.id)}`,
    {
      method: "PATCH",
      headers: { AccessKey: key, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
  const patchText = await patchRes.text();
  if (!patchRes.ok) {
    throw new Error(`PATCH /containers/${t.id} ${patchRes.status}: ${patchText}`);
  }
  console.log("PATCH OK (rolling update should start):", patchText.slice(0, 300));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
