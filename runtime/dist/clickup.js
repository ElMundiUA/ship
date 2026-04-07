/**
 * ClickUp REST adapter (list tasks + tags). Status names must match your list’s workflow.
 */
import { READY_LABELS, FLOW_LABELS } from "./agent-contracts.js";
import { hasLabel, matchesFilters } from "./tracker-common.js";

export class ClickUpTracker {
  static fromEnv(config) {
    const c = config.tracker?.clickup ?? {};
    const tokenEnv = c.tokenEnv ?? "CLICKUP_API_TOKEN";
    const listEnv = c.listIdEnv ?? "CLICKUP_LIST_ID";
    const teamEnv = c.teamIdEnv ?? "CLICKUP_TEAM_ID";
    const token = (process.env[tokenEnv] ?? "").trim();
    const listId = (process.env[listEnv] ?? "").trim();
    const teamId = (process.env[teamEnv] ?? "").trim();
    if (!token || !listId) return null;
    return new ClickUpTracker(token, listId, teamId || null);
  }

  constructor(token, listId, teamId) {
    this.token = token;
    this.listId = listId;
    this.teamId = teamId;
    this.base = "https://api.clickup.com/api/v2";
  }

  headers(json = true) {
    const h = { Authorization: this.token };
    if (json) h["Content-Type"] = "application/json";
    return h;
  }

  async req(method, path, body) {
    const init = {
      method,
      headers: this.headers(body !== undefined),
    };
    if (body !== undefined) init.body = JSON.stringify(body);
    const r = await fetch(`${this.base}${path}`, init);
    const text = await r.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {
      /* ignore */
    }
    if (!r.ok) {
      throw new Error(`ClickUp ${method} ${path} → ${r.status}: ${text.slice(0, 500)}`);
    }
    return json;
  }

  toCanonical(task) {
    const tags = task.tags ?? [];
    const nodes = tags.map((t) => ({ name: typeof t === "string" ? t : t.name ?? String(t) }));
    return {
      id: String(task.id),
      identifier: task.custom_id ? String(task.custom_id) : String(task.id),
      title: task.name ?? "",
      description: task.text_content ?? task.description ?? undefined,
      state: { name: task.status?.status ?? task.status?.type ?? "unknown" },
      labels: { nodes },
      url: task.url,
    };
  }

  async getIssue(taskId) {
    const j = await this.req("GET", `/task/${encodeURIComponent(taskId)}`);
    return j ? this.toCanonical(j) : undefined;
  }

  async getIssueByIdentifier(ref) {
    const s = String(ref).trim();
    try {
      return await this.getIssue(s);
    } catch (first) {
      if (!this.teamId) throw first;
      const j = await this.req(
        "GET",
        `/task/${encodeURIComponent(s)}?custom_task_ids=true&team_id=${encodeURIComponent(this.teamId)}`
      );
      return this.toCanonical(j);
    }
  }

  async listAllTasksInList() {
    const out = [];
    for (let page = 0; page < 40; page++) {
      const j = await this.req(
        "GET",
        `/list/${encodeURIComponent(this.listId)}/task?page=${page}&include_closed=false&subtasks=true`
      );
      const tasks = j.tasks ?? [];
      out.push(...tasks);
      if (tasks.length < 100) break;
    }
    return out;
  }

  async listIssues(filters, limit = 50) {
    const raw = await this.listAllTasksInList();
    let canon = raw.map((t) => this.toCanonical(t));
    if (filters.role) {
      canon = canon.filter((i) => hasLabel(i, READY_LABELS[filters.role]));
    }
    if (filters.withoutRole === "ba") {
      canon = canon.filter((i) => hasLabel(i, FLOW_LABELS.noBa));
    }
    if (filters.status || filters.labels?.length) {
      canon = canon.filter((i) => matchesFilters(i, filters));
    }
    return canon.slice(0, limit);
  }

  async getNextIssueForRole(role, withoutBa) {
    const list = await this.listIssues({ role, withoutRole: withoutBa ? "ba" : undefined }, 80);
    if (withoutBa) {
      return list.find((i) => hasLabel(i, FLOW_LABELS.noBa)) ?? list[0];
    }
    return list[0];
  }

  async updateIssueState(taskId, stateName) {
    await this.req("PUT", `/task/${encodeURIComponent(taskId)}`, { status: stateName });
    return true;
  }

  async addLabel(taskId, labelName, _config) {
    await this.req("POST", `/task/${encodeURIComponent(taskId)}/tag/${encodeURIComponent(labelName)}`);
    return true;
  }

  async removeLabel(taskId, labelName) {
    await this.req(
      "DELETE",
      `/task/${encodeURIComponent(taskId)}/tag/${encodeURIComponent(labelName)}`
    );
    return true;
  }

  async addComment(taskId, body) {
    const j = await this.req("POST", `/task/${encodeURIComponent(taskId)}/comment`, {
      comment_text: body,
      notify_all: false,
    });
    return j?.id;
  }
}
