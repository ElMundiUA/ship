/**
 * Azure DevOps Boards (Work Items) REST adapter.
 * Tags map to ship labels (same names as Linear: ready:developer, flow:no-ba, …).
 * System.State must match your process template (e.g. In Progress, Done).
 */
import { READY_LABELS, FLOW_LABELS } from "./agent-contracts.js";
import { hasLabel, matchesFilters } from "./tracker-common.js";

function splitTags(tagsField) {
  if (!tagsField || typeof tagsField !== "string") return [];
  return tagsField
    .split(";")
    .map((t) => t.trim())
    .filter(Boolean);
}

export class AzureBoardsTracker {
  static fromEnv(config) {
    const a = config.tracker?.azureDevops ?? config.tracker?.["azure-devops"] ?? {};
    const orgEnv = a.orgEnv ?? "AZURE_DEVOPS_ORG";
    const projectEnv = a.projectEnv ?? "AZURE_DEVOPS_PROJECT";
    const patEnv = a.patEnv ?? "AZURE_DEVOPS_PAT";
    const org = (process.env[orgEnv] ?? "").trim();
    const project = (process.env[projectEnv] ?? "").trim();
    const pat = (process.env[patEnv] ?? "").trim();
    if (!org || !project || !pat) return null;
    return new AzureBoardsTracker(org, project, pat);
  }

  constructor(org, project, pat) {
    this.org = org;
    this.project = encodeURIComponent(project);
    this.pat = pat;
    this.base = `https://dev.azure.com/${org}`;
    this.auth = `Basic ${Buffer.from(`:${pat}`).toString("base64")}`;
    this.api = "7.1";
  }

  async req(method, path, body, apiVersion) {
    const ver = apiVersion ?? this.api;
    const url = `${this.base}${path}${path.includes("?") ? "&" : "?"}api-version=${ver}`;
    const headers = {
      Authorization: this.auth,
      Accept: "application/json",
    };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const r = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await r.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {
      /* ignore */
    }
    if (!r.ok) {
      throw new Error(`Azure DevOps ${method} ${path} → ${r.status}: ${text.slice(0, 500)}`);
    }
    return json;
  }

  toCanonical(fields, id) {
    const tags = splitTags(fields["System.Tags"]);
    return {
      id: String(id),
      identifier: String(id),
      title: fields["System.Title"] ?? "",
      description: fields["System.Description"] ?? undefined,
      state: { name: fields["System.State"] ?? "Unknown" },
      labels: { nodes: tags.map((name) => ({ name })) },
      url: `https://dev.azure.com/${this.org}/${decodeURIComponent(this.project)}/_workitems/edit/${id}`,
    };
  }

  async getWorkItem(id) {
    const path = `/${this.project}/_apis/wit/workitems/${encodeURIComponent(id)}?$expand=all`;
    const j = await this.req("GET", path);
    return this.toCanonical(j.fields ?? {}, j.id);
  }

  async getIssue(issueId) {
    return this.getWorkItem(issueId);
  }

  async getIssueByIdentifier(ref) {
    const n = String(ref).replace(/^#/, "").trim();
    if (/^\d+$/.test(n)) return this.getWorkItem(n);
    return this.getWorkItem(ref);
  }

  async wiql(query) {
    const j = await this.req("POST", `/${this.project}/_apis/wit/wiql`, { query });
    return (j.workItems ?? []).map((w) => w.id);
  }

  async batchGet(ids) {
    if (ids.length === 0) return [];
    const j = await this.req("POST", `/${this.project}/_apis/wit/workitemsbatch`, {
      ids: ids.slice(0, 200),
      fields: [
        "System.Id",
        "System.Title",
        "System.State",
        "System.Description",
        "System.Tags",
      ],
    });
    return (j.value ?? []).map((item) => this.toCanonical(item.fields ?? {}, item.id));
  }

  buildWiqlBase(extraAnd = "") {
    const and = extraAnd ? ` AND ${extraAnd}` : "";
    return `SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project AND [System.WorkItemType] <> '' AND [System.State] <> 'Closed' AND [System.State] <> 'Removed'${and} ORDER BY [System.ChangedDate] DESC`;
  }

  async listIssues(filters, limit = 50) {
    const parts = [];
    if (filters.role) {
      parts.push(`[System.Tags] CONTAINS '${READY_LABELS[filters.role].replace(/'/g, "''")}'`);
    }
    if (filters.withoutRole === "ba") {
      parts.push(`[System.Tags] CONTAINS '${FLOW_LABELS.noBa.replace(/'/g, "''")}'`);
    }
    const tagFilter = parts.join(" AND ");
    const query = this.buildWiqlBase(tagFilter);
    const ids = await this.wiql(query);
    let items = await this.batchGet(ids.slice(0, Math.min(limit + 25, 200)));
    if (filters.withoutRole === "ba" && filters.role) {
      items = items.filter((i) => hasLabel(i, FLOW_LABELS.noBa));
    }
    if (filters.status || filters.labels?.length) {
      items = items.filter((i) => matchesFilters(i, filters));
    }
    return items.slice(0, limit);
  }

  async getNextIssueForRole(role, withoutBa) {
    const parts = [`[System.Tags] CONTAINS '${READY_LABELS[role].replace(/'/g, "''")}'`];
    if (withoutBa) {
      parts.push(`[System.Tags] CONTAINS '${FLOW_LABELS.noBa.replace(/'/g, "''")}'`);
    }
    const query = this.buildWiqlBase(parts.join(" AND "));
    const ids = await this.wiql(query);
    if (ids.length === 0) return undefined;
    const items = await this.batchGet(ids.slice(0, 50));
    if (withoutBa) {
      return items.find((i) => hasLabel(i, FLOW_LABELS.noBa)) ?? items[0];
    }
    return items[0];
  }

  async updateIssueState(issueId, stateName) {
    await this.req(
      "PATCH",
      `/${this.project}/_apis/wit/workitems/${encodeURIComponent(issueId)}`,
      [{ op: "replace", path: "/fields/System.State", value: stateName }]
    );
    return true;
  }

  async addLabel(issueId, labelName, _config) {
    const cur = await this.req(
      "GET",
      `/${this.project}/_apis/wit/workitems/${encodeURIComponent(issueId)}?$fields=System.Tags`
    );
    const tags = splitTags(cur.fields?.["System.Tags"]);
    if (!tags.includes(labelName)) tags.push(labelName);
    const val = tags.join("; ");
    try {
      await this.req("PATCH", `/${this.project}/_apis/wit/workitems/${encodeURIComponent(issueId)}`, [
        { op: "replace", path: "/fields/System.Tags", value: val },
      ]);
    } catch {
      await this.req("PATCH", `/${this.project}/_apis/wit/workitems/${encodeURIComponent(issueId)}`, [
        { op: "add", path: "/fields/System.Tags", value: val },
      ]);
    }
    return true;
  }

  async removeLabel(issueId, labelName) {
    const cur = await this.req(
      "GET",
      `/${this.project}/_apis/wit/workitems/${encodeURIComponent(issueId)}?$fields=System.Tags`
    );
    const tags = splitTags(cur.fields?.["System.Tags"]).filter((t) => t !== labelName);
    const val = tags.join("; ");
    try {
      await this.req("PATCH", `/${this.project}/_apis/wit/workitems/${encodeURIComponent(issueId)}`, [
        { op: "replace", path: "/fields/System.Tags", value: val },
      ]);
    } catch {
      await this.req("PATCH", `/${this.project}/_apis/wit/workitems/${encodeURIComponent(issueId)}`, [
        { op: "add", path: "/fields/System.Tags", value: val },
      ]);
    }
    return true;
  }

  async addComment(issueId, body) {
    const j = await this.req(
      "POST",
      `/${this.project}/_apis/wit/workItems/${encodeURIComponent(issueId)}/comments`,
      { text: body },
      "7.1-preview.4"
    );
    return j?.id ?? j?.commentId;
  }
}
