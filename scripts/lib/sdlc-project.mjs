/**
 * SDLC pick scripts: restrict issues to one Linear project (delivery lane).
 * Configure with LINEAR_SDLC_PROJECT_ID (preferred) or LINEAR_SDLC_PROJECT_NAME (lookup).
 * No org-specific defaults — set env or `.env` for your workspace.
 */
import { linearGraphql } from "./linear-fetch.mjs";

/**
 * @param {(k: string) => string | undefined} getEnv - from process.env or loaded .env
 * @returns {Promise<string|null>} project id or null if unresolved
 */
export async function resolveSdlcProjectId(apiKey, getEnv) {
  const id = (getEnv("LINEAR_SDLC_PROJECT_ID") || "").trim();
  if (id) return id;

  const name = (getEnv("LINEAR_SDLC_PROJECT_NAME") || "").trim();
  if (!name) {
    console.warn(
      "resolveSdlcProjectId: set LINEAR_SDLC_PROJECT_ID or LINEAR_SDLC_PROJECT_NAME (see docs Examples → reference org / .env.example)."
    );
    return null;
  }

  try {
    const data = await linearGraphql(
      apiKey,
      `query($filter: ProjectFilter!) {
        projects(filter: $filter, first: 20) {
          nodes { id name }
        }
      }`,
      { filter: { name: { eq: name } } }
    );
    const nodes = data.projects?.nodes ?? [];
    let hit = nodes.find((p) => p.name === name);
    if (!hit) {
      const lower = name.toLowerCase();
      hit = nodes.find((p) => p.name?.toLowerCase() === lower);
    }
    if (hit?.id) return hit.id;
  } catch {
    /* fall through */
  }

  console.warn(
    `resolveSdlcProjectId: no Linear project named "${name}". Set LINEAR_SDLC_PROJECT_ID to the project UUID.`
  );
  return null;
}

/** @param {Record<string, unknown>} filter - Linear IssueFilter fragment */
export function withSdlcProject(filter, projectId) {
  if (!projectId) return filter;
  return { ...filter, project: { id: { eq: projectId } } };
}
