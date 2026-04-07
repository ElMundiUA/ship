/**
 * GitHub Issues REST adapter (repos/{owner}/{repo}/issues).
 * Workflow “state” for open issues: exclusive label ship-status:<name> (see tracker-common).
 */
import { READY_LABELS, FLOW_LABELS } from "./agent-contracts.js";
import {
  hasLabel,
  matchesFilters,
  githubStatusLabel,
  parseGithubWorkflowState,
  GITHUB_STATUS_PREFIX,
} from "./tracker-common.js";

function parseRepoFull(full) {
  const s = (full ?? "").trim();
  const m = s.match(/^([^/]+)\/([^/]+)$/);
  if (!m) return null;
  return { owner: m[1], repo: m[2] };
}

export class GitHubIssuesTracker {
  static fromEnv(config) {
    const g = config.tracker?.github ?? {};
    const tokenEnv = g.tokenEnv ?? "GITHUB_TOKEN";
    const altTokenEnv = g.altTokenEnv ?? "GITHUB_ISSUES_TOKEN";
    const repoEnv = g.repoEnv ?? "GITHUB_REPOSITORY";
    const token = (process.env[altTokenEnv] ?? process.env[tokenEnv] ?? "").trim();
    const full = (process.env[repoEnv] ?? "").trim();
    const ownerEnv = g.ownerEnv ?? "GITHUB_ISSUES_OWNER";
    const repoOnlyEnv = g.repoNameEnv ?? "GITHUB_ISSUES_REPO";
    let owner;
    let repo;
    const parsed = parseRepoFull(full);
    if (parsed) {
      owner = parsed.owner;
      repo = parsed.repo;
    } else {
      owner = (process.env[ownerEnv] ?? "").trim();
      repo = (process.env[repoOnlyEnv] ?? "").trim();
    }
    if (!token || !owner || !repo) return null;
    return new GitHubIssuesTracker(token, owner, repo);
  }

  constructor(token, owner, repo) {
    this.token = token;
    this.owner = owner;
    this.repo = repo;
    this.base = "https://api.github.com";
  }

  async req(method, path, body) {
    const url = path.startsWith("http") ? path : `${this.base}${path}`;
    const headers = {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${this.token}`,
      "X-GitHub-Api-Version": "2022-11-28",
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
      throw new Error(`GitHub ${method} ${path} → ${r.status}: ${text.slice(0, 500)}`);
    }
    return json;
  }

  issuePath(numOrId) {
    return `/repos/${this.owner}/${this.repo}/issues/${encodeURIComponent(numOrId)}`;
  }

  toCanonical(issue) {
    const labels = (issue.labels ?? []).map((l) => ({ name: l.name }));
    const stateName = parseGithubWorkflowState(issue.labels, issue.state === "closed");
    const num = issue.number;
    return {
      id: String(num),
      identifier: String(num),
      title: issue.title ?? "",
      description: issue.body ?? undefined,
      state: { name: stateName },
      labels: { nodes: labels },
      assignee: issue.assignee ? { name: issue.assignee.login } : undefined,
      url: issue.html_url,
    };
  }

  async ensureLabelExists(name) {
    try {
      await this.req(
        "POST",
        `/repos/${this.owner}/${this.repo}/labels`,
        { name, color: "ededed", description: "ship-agent workflow" }
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (!/\b422\b/.test(msg) && !/already exists/i.test(msg)) throw e;
    }
  }

  async getIssue(issueIdOrNumber) {
    const data = await this.req("GET", `${this.issuePath(issueIdOrNumber)}`);
    if (data.pull_request) return undefined;
    return this.toCanonical(data);
  }

  async getIssueByIdentifier(ref) {
    const n = String(ref).replace(/^#/, "").trim();
    if (/^\d+$/.test(n)) return this.getIssue(n);
    return this.getIssue(ref);
  }

  searchQuery(extraLabels = []) {
    const parts = [`repo:${this.owner}/${this.repo}`, "type:issue", "state:open"];
    for (const lab of extraLabels) {
      parts.push(`label:"${lab.replace(/"/g, '\\"')}"`);
    }
    return parts.join(" ");
  }

  async searchIssues(extraLabels, perPage = 100) {
    const q = this.searchQuery(extraLabels);
    const params = new URLSearchParams({
      q,
      per_page: String(Math.min(perPage, 100)),
      sort: "updated",
      order: "desc",
    });
    const data = await this.req("GET", `/search/issues?${params}`);
    return (data.items ?? []).filter((i) => !i.pull_request);
  }

  async listIssues(filters, limit = 50) {
    const extra = [];
    if (filters.role) extra.push(READY_LABELS[filters.role]);
    if (filters.withoutRole === "ba") extra.push(FLOW_LABELS.noBa);
    const issues = await this.searchIssues(extra, Math.min(100, limit + 20));
    let canon = issues.map((i) => this.toCanonical(i));
    if (filters.withoutRole === "ba" && filters.role) {
      canon = canon.filter((i) => hasLabel(i, FLOW_LABELS.noBa));
    }
    if (filters.status || filters.labels?.length) {
      canon = canon.filter((i) => matchesFilters(i, filters));
    }
    return canon.slice(0, limit);
  }

  async getNextIssueForRole(role, withoutBa) {
    const extra = [READY_LABELS[role]];
    if (withoutBa) extra.push(FLOW_LABELS.noBa);
    const issues = await this.searchIssues(extra, 50);
    const canon = issues.map((i) => this.toCanonical(i));
    if (withoutBa) {
      return canon.find((i) => hasLabel(i, FLOW_LABELS.noBa)) ?? canon[0];
    }
    return canon[0];
  }

  async updateIssueState(issueIdOrNumber, stateName) {
    const cur = await this.req("GET", `${this.issuePath(issueIdOrNumber)}`);
    if (cur.pull_request) return false;
    const target = stateName.trim();
    const lower = target.toLowerCase();
    if (lower === "done" || lower === "canceled" || lower === "cancelled") {
      await this.req("PATCH", this.issuePath(issueIdOrNumber), { state: "closed" });
      await this.stripStatusLabels(cur.number, cur.labels ?? []);
      return true;
    }
    await this.req("PATCH", this.issuePath(issueIdOrNumber), { state: "open" });
    const labels = (cur.labels ?? []).map((l) => l.name);
    const next = labels.filter((n) => !n.startsWith(GITHUB_STATUS_PREFIX));
    const statusLabel = githubStatusLabel(target);
    await this.ensureLabelExists(statusLabel);
    if (!next.includes(statusLabel)) next.push(statusLabel);
    await this.req("PATCH", this.issuePath(issueIdOrNumber), { labels: next });
    return true;
  }

  async stripStatusLabels(issueNumber, labelObjs) {
    const names = (labelObjs ?? []).map((l) => l.name).filter((n) => !n.startsWith(GITHUB_STATUS_PREFIX));
    await this.req("PATCH", this.issuePath(issueNumber), { labels: names });
  }

  async addLabel(issueIdOrNumber, labelName, _config) {
    await this.ensureLabelExists(labelName);
    await this.req("POST", `${this.issuePath(issueIdOrNumber)}/labels`, {
      labels: [labelName],
    });
    return true;
  }

  async removeLabel(issueIdOrNumber, labelName) {
    await this.req(
      "DELETE",
      `${this.issuePath(issueIdOrNumber)}/labels/${encodeURIComponent(labelName)}`
    );
    return true;
  }

  async addComment(issueIdOrNumber, body) {
    const res = await this.req("POST", `${this.issuePath(issueIdOrNumber)}/comments`, { body });
    return res?.id;
  }
}
