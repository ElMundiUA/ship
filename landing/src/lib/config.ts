function envOr(key: `NEXT_PUBLIC_${string}`, fallback: string): string {
  const raw = process.env[key] ?? "";
  const v = raw.replace(/[\u0000-\u001F\u007F-\u009F]/g, "").trim();
  return v || fallback;
}

/** Canonical site origin (metadata, OpenGraph). Docs live on the same Next app under `/docs`. */
export const siteUrl = envOr("NEXT_PUBLIC_SITE_URL", "http://127.0.0.1:3000");

/** @deprecated Prefer same-origin `/docs/...` routes. Kept for env override when splitting hosts. */
export const docsUrl = envOr("NEXT_PUBLIC_DOCS_URL", siteUrl);

export const repoUrl = envOr("NEXT_PUBLIC_REPO_URL", "https://github.com/ElMundiUA/ship");

/** Methodology API (FastAPI) — search, fetch, feedback, GET /patterns, /tools, /collections. */
export const shipApiBase = envOr("NEXT_PUBLIC_SHIP_API_BASE", "http://127.0.0.1:8100");
