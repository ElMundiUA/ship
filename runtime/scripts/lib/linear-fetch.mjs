/**
 * Minimal Linear GraphQL helper for pick scripts.
 */
export async function linearGraphql(apiKey, query, variables = {}) {
  const res = await fetch("https://api.linear.app/graphql", {
    method: "POST",
    headers: { Authorization: apiKey, "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors) throw new Error(JSON.stringify(json.errors));
  return json.data;
}

export function loadLinearEnv() {
  const apiKey = process.env.LINEAR_API_KEY;
  const teamKey = process.env.LINEAR_TEAM_KEY || "ELM";
  if (!apiKey) return { apiKey: null, teamKey, teamId: null };
  return { apiKey, teamKey, teamId: null };
}

/** In GitHub Actions, fail fast if Linear key is missing (no silent empty pick). */
export function exitIfMissingLinearKeyInCi(apiKey) {
  if (process.env.GITHUB_ACTIONS !== "true") return;
  if (apiKey) return;
  console.error("MISSING_LINEAR_API_KEY");
  process.exit(1);
}

export async function resolveTeam(linearGraphqlFn, apiKey, teamKey) {
  const data = await linearGraphqlFn(apiKey, `query { teams(first: 50) { nodes { id key } } }`);
  const teams = data.teams?.nodes ?? [];
  const key = String(teamKey || "ELM").trim() || "ELM";
  const team = teams.find((t) => t.key?.toLowerCase() === key.toLowerCase());
  if (team) return team;
  if (teams.length === 1) {
    console.warn(`resolveTeam: no team "${key}", using only workspace team ${teams[0].key}`);
    return teams[0];
  }
  console.error(`resolveTeam: no team matching "${key}". Available: ${teams.map((t) => t.key).join(", ")}`);
  return null;
}
