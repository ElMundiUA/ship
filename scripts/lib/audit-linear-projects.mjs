/**
 * Resolve Linear project IDs for daily audit roles (tech debt / security).
 * Env: LINEAR_TECH_DEBT_PROJECT_ID | LINEAR_TECH_DEBT_PROJECT_NAME (default "ElMundi tech debt")
 *      LINEAR_SECURITY_PROJECT_ID | LINEAR_SECURITY_PROJECT_NAME (default "ElMundi security")
 */
import { linearGraphql } from "./linear-fetch.mjs";

export const DEFAULT_TECH_DEBT_PROJECT_NAME = "ElMundi tech debt";
export const DEFAULT_SECURITY_PROJECT_NAME = "ElMundi security";

/**
 * @param {(k: string) => string | undefined} getEnv
 * @returns {Promise<string|null>}
 */
export async function resolveTechDebtProjectId(apiKey, getEnv) {
  const id = (getEnv("LINEAR_TECH_DEBT_PROJECT_ID") || "").trim();
  if (id) return id;
  const name = (getEnv("LINEAR_TECH_DEBT_PROJECT_NAME") || DEFAULT_TECH_DEBT_PROJECT_NAME).trim();
  return resolveProjectIdByName(linearGraphql, apiKey, name);
}

/**
 * @param {(k: string) => string | undefined} getEnv
 * @returns {Promise<string|null>}
 */
export async function resolveSecurityProjectId(apiKey, getEnv) {
  const id = (getEnv("LINEAR_SECURITY_PROJECT_ID") || "").trim();
  if (id) return id;
  const name = (getEnv("LINEAR_SECURITY_PROJECT_NAME") || DEFAULT_SECURITY_PROJECT_NAME).trim();
  return resolveProjectIdByName(linearGraphql, apiKey, name);
}

async function resolveProjectIdByName(linearGraphqlFn, apiKey, name) {
  try {
    const data = await linearGraphqlFn(
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
    return hit?.id ?? null;
  } catch {
    return null;
  }
}
