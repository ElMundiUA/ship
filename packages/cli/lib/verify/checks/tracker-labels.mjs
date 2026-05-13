import fs from "node:fs";
import path from "node:path";
import YAML from "yaml";

export const id = "tracker-labels";
export const category = "network";
export const description = "Tracker labels match .ship/labels.yml";

async function fetchLinearLabels(apiKey) {
  const body = JSON.stringify({
    query: "query { issueLabels(first: 250) { nodes { id name } } }",
  });
  const res = await fetch("https://api.linear.app/graphql", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      Authorization: apiKey,
    },
    body,
  });
  if (!res.ok) {
    throw new Error(`Linear API ${res.status} ${res.statusText}`);
  }
  const data = await res.json();
  const nodes = data?.data?.issueLabels?.nodes || [];
  return nodes.map((n) => String(n.name));
}

function readLabelsYaml(cwd) {
  const rel = path.join(".ship", "labels.yml");
  const abs = path.join(cwd, rel);
  if (!fs.existsSync(abs)) return null;
  try {
    const doc = YAML.parse(fs.readFileSync(abs, "utf8"));
    const arr = Array.isArray(doc?.labels) ? doc.labels : [];
    return arr.map((l) => (typeof l === "string" ? l : l && l.name)).filter(Boolean);
  } catch {
    return [];
  }
}

/**
 * @param {import("../registry.mjs").CheckContext} ctx
 */
export async function run(ctx) {
  const tracker = ctx.config && ctx.config.stack && ctx.config.stack.tracker;
  if (!tracker || tracker === "none") {
    return { status: "skip", detail: "stack.tracker is none" };
  }
  if (tracker !== "linear") {
    return {
      status: "skip",
      detail: `tracker=${tracker} label verification not implemented yet`,
    };
  }

  const declared = readLabelsYaml(ctx.cwd);
  if (declared == null) {
    return { status: "skip", detail: ".ship/labels.yml not present" };
  }

  const apiKey = process.env.LINEAR_API_KEY;
  if (!apiKey) {
    return {
      status: "skip",
      detail: "LINEAR_API_KEY not set — skipping live Linear label fetch",
    };
  }

  let remote;
  try {
    remote = await fetchLinearLabels(apiKey);
  } catch (e) {
    return { status: "warn", detail: `Linear API call failed: ${e.message}` };
  }

  const remoteSet = new Set(remote);
  const missing = declared.filter((n) => !remoteSet.has(n));
  if (!missing.length) {
    return {
      status: "pass",
      detail: `all ${declared.length} declared labels exist on Linear`,
    };
  }
  return {
    status: "warn",
    detail: `missing labels on Linear: ${missing.join(", ")}`,
    data: { missing, declared, remote_count: remote.length },
  };
}
