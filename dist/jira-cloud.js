/**
 * Jira Cloud REST API v3 — second tracker adapter for ship-agent CLI.
 * Auth: JIRA_PAT (Bearer) or JIRA_EMAIL + JIRA_API_TOKEN (Basic), per Atlassian Cloud.
 */
import { READY_LABELS, FLOW_LABELS } from "./agent-contracts.js";
import { hasLabel, matchesFilters } from "./tracker-common.js";

function extractTextFromAdf(node) {
  if (!node) return "";
  if (typeof node === "string") return node;
  if (node.text) return node.text;
  if (Array.isArray(node.content)) {
    return node.content.map(extractTextFromAdf).join("");
  }
  return "";
}

function plainCommentBody(text) {
  return {
    type: "doc",
    version: 1,
    content: [{ type: "paragraph", content: [{ type: "text", text }] }],
  };
}

export class JiraCloudTracker {
  static fromEnv(config) {
    const j = config.tracker?.jira ?? {};
    const hostEnv = j.hostEnv ?? "JIRA_HOST";
    const patEnv = j.patEnv ?? "JIRA_PAT";
    const emailEnv = j.emailEnv ?? "JIRA_EMAIL";
    const tokenEnv = j.tokenEnv ?? "JIRA_API_TOKEN";
    const host = (process.env[hostEnv] ?? "").trim();
    const pat = (process.env[patEnv] ?? "").trim();
    const email = (process.env[emailEnv] ?? "").trim();
    const token = (process.env[tokenEnv] ?? "").trim();
    if (!host) return null;
    const base = host.startsWith("http") ? host.replace(/\/$/, "") : `https://${host}`;
    const headers = { Accept: "application/json", "Content-Type": "application/json" };
    if (pat) {
      headers.Authorization = `Bearer ${pat}`;
    } else if (email && token) {
      headers.Authorization = `Basic ${Buffer.from(`${email}:${token}`).toString("base64")}`;
    } else {
      return null;
    }
    return new JiraCloudTracker(base, headers);
  }

  constructor(base, headers) {
    this.base = base;
    this.headers = headers;
  }

  async req(method, path, body) {
    const url = `${this.base}/rest/api/3${path}`;
    const r = await fetch(url, {
      method,
      headers: this.headers,
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
      throw new Error(`Jira ${method} ${path} → ${r.status}: ${text.slice(0, 500)}`);
    }
    return json;
  }

  toCanonical(fields, id, key) {
    const labels = fields.labels ?? [];
    const statusName = fields.status?.name ?? "Unknown";
    let desc = "";
    if (typeof fields.description === "string") {
      desc = fields.description;
    } else if (fields.description?.content) {
      desc = extractTextFromAdf(fields.description);
    }
    return {
      id,
      identifier: key,
      title: fields.summary ?? "",
      description: desc || undefined,
      state: { name: statusName },
      labels: { nodes: labels.map((name) => ({ name })) },
      assignee: fields.assignee ? { name: fields.assignee.displayName } : undefined,
      priority: fields.priority?.id,
      url: `${this.base}/browse/${key}`,
    };
  }

  async getIssue(issueIdOrKey) {
    const j = await this.req(
      "GET",
      `/issue/${encodeURIComponent(issueIdOrKey)}?fields=summary,description,status,labels,assignee,priority`
    );
    return this.toCanonical(j.fields, j.id, j.key);
  }

  async getIssueByIdentifier(ref) {
    return this.getIssue(ref);
  }

  async searchWithJql(jql, maxResults) {
    const j = await this.req("POST", "/search", {
      jql,
      maxResults: Math.min(maxResults, 100),
      fields: ["summary", "description", "status", "labels", "assignee", "priority"],
    });
    return (j.issues ?? []).map((issue) => this.toCanonical(issue.fields, issue.id, issue.key));
  }

  async listIssues(filters, limit = 50) {
    const parts = ["resolution = Unresolved", "statusCategory != Done"];
    if (filters.role) {
      parts.push(`labels = "${READY_LABELS[filters.role]}"`);
    } else if (filters.withoutRole === "ba") {
      parts.push(`labels = "${FLOW_LABELS.noBa}"`);
    }
    const jql = parts.join(" AND ");
    let issues = await this.searchWithJql(jql, Math.min(limit, 100));
    if (filters.withoutRole === "ba" && filters.role) {
      issues = issues.filter((i) => hasLabel(i, FLOW_LABELS.noBa));
    }
    if (filters.status || filters.labels?.length) {
      issues = issues.filter((i) => matchesFilters(i, filters));
    }
    return issues.slice(0, limit);
  }

  async getNextIssueForRole(role, withoutBa) {
    const parts = [
      "resolution = Unresolved",
      "statusCategory != Done",
      `labels = "${READY_LABELS[role]}"`,
    ];
    if (withoutBa) {
      parts.push(`labels = "${FLOW_LABELS.noBa}"`);
    }
    const jql = parts.join(" AND ");
    const issues = await this.searchWithJql(jql, withoutBa ? 50 : 10);
    if (withoutBa) {
      return issues.find((i) => hasLabel(i, FLOW_LABELS.noBa)) ?? issues[0];
    }
    return issues[0];
  }

  async updateIssueState(issueIdOrKey, stateName) {
    const issue = await this.getIssue(issueIdOrKey);
    const key = issue.identifier;
    const tr = await this.req("GET", `/issue/${encodeURIComponent(key)}/transitions`);
    const nodes = tr.transitions ?? [];
    const target = stateName.trim().toLowerCase();
    const transition = nodes.find((t) => (t.to?.name ?? "").toLowerCase() === target);
    if (!transition) {
      const names = nodes.map((t) => t.to?.name).filter(Boolean);
      console.error(
        `updateIssueState (Jira): no transition to "${stateName}" for ${key}. Available targets: ${names.join(", ") || "(none)"}`
      );
      return false;
    }
    await this.req("POST", `/issue/${encodeURIComponent(key)}/transitions`, {
      transition: { id: transition.id },
    });
    return true;
  }

  async addLabel(issueIdOrKey, labelName, _config) {
    const issue = await this.getIssue(issueIdOrKey);
    const key = issue.identifier;
    const labels = new Set((issue.labels?.nodes ?? []).map((l) => l.name));
    labels.add(labelName);
    await this.req("PUT", `/issue/${encodeURIComponent(key)}`, {
      fields: { labels: [...labels] },
    });
    return true;
  }

  async removeLabel(issueIdOrKey, labelName) {
    const issue = await this.getIssue(issueIdOrKey);
    const key = issue.identifier;
    const labels = (issue.labels?.nodes ?? []).map((l) => l.name).filter((n) => n !== labelName);
    await this.req("PUT", `/issue/${encodeURIComponent(key)}`, {
      fields: { labels },
    });
    return true;
  }

  async addComment(issueIdOrKey, body) {
    const issue = await this.getIssue(issueIdOrKey);
    const key = issue.identifier;
    const res = await this.req("POST", `/issue/${encodeURIComponent(key)}/comment`, {
      body: plainCommentBody(body),
    });
    return res?.id;
  }
}
