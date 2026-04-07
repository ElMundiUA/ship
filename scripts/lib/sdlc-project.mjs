/**
 * SDLC pick scripts: restrict issues to one Linear project (pre-release lane).
 * Override: LINEAR_SDLC_PROJECT_ID, or LINEAR_SDLC_PROJECT_NAME for lookup.
 */
import { linearGraphql } from "./linear-fetch.mjs";

/** Canonical ElMundi pre-release project (see create-prerelease-e2e-bugs.mjs). */
export const DEFAULT_SDLC_PROJECT_ID = "2eead1a7-8585-4678-96e9-6b3f86b6534c";

const DEFAULT_PROJECT_NAME = "ElMundi pre-release";

/**
 * @param {(k: string) => string | undefined} getEnv - from process.env or loaded .env
 * @returns {Promise<string|null>} project id or null if unresolved
 */
export async function resolveSdlcProjectId(apiKey, getEnv) {
  const id = (getEnv("LINEAR_SDLC_PROJECT_ID") || "").trim();
  if (id) return id;

  const name = (getEnv("LINEAR_SDLC_PROJECT_NAME") || DEFAULT_PROJECT_NAME).trim();
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
    // fall through to default
  }

  console.warn(
    `resolveSdlcProjectId: no project named "${name}", using DEFAULT_SDLC_PROJECT_ID. Set LINEAR_SDLC_PROJECT_ID to override.`
  );
  return DEFAULT_SDLC_PROJECT_ID;
}

/** @param {Record<string, unknown>} filter - Linear IssueFilter fragment */
export function withSdlcProject(filter, projectId) {
  if (!projectId) return filter;
  return { ...filter, project: { id: { eq: projectId } } };
}
