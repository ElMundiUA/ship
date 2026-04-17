import { listCached, verifyCached } from "../../cache/store.mjs";

export const id = "cache-integrity";
export const category = "local";
export const description = "Cached artifact bodies match their .meta.json sha256";

/**
 * @param {import("../registry.mjs").CheckContext} ctx
 */
export async function run(ctx) {
  let entries = [];
  try {
    entries = listCached(ctx.cwd);
  } catch (e) {
    return { status: "fail", detail: `failed to read cache index: ${e.message}` };
  }
  if (!entries.length) {
    return { status: "skip", detail: "no cached artifacts (.ship/cache/)" };
  }

  const tampered = [];
  for (const e of entries) {
    const res = verifyCached(ctx.cwd, e.kind, e.id, e.version);
    if (!res.ok) {
      tampered.push({
        kind: e.kind,
        id: e.id,
        version: e.version,
        expected: res.expected,
        actual: res.actual,
        reason: res.reason || "sha256 mismatch",
      });
    }
  }

  if (tampered.length) {
    return {
      status: "fail",
      detail: `${tampered.length}/${entries.length} cached entries tampered: ${tampered
        .slice(0, 3)
        .map((t) => `${t.kind}/${t.id}@${t.version}`)
        .join(", ")}${tampered.length > 3 ? "…" : ""}`,
      data: { tampered, total: entries.length },
    };
  }
  return {
    status: "pass",
    detail: `${entries.length} cached entries verified (sha256 ok)`,
    data: { total: entries.length },
  };
}
