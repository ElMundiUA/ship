/**
 * Tracker facade — Linear, Jira Cloud, GitHub Issues, Azure Boards, ClickUp (same CLI surface).
 */
import * as linear from "./linear-client.js";
import { JiraCloudTracker } from "./jira-cloud.js";
import { GitHubIssuesTracker } from "./github-issues.js";
import { AzureBoardsTracker } from "./azure-boards.js";
import { ClickUpTracker } from "./clickup.js";

const PROVIDERS = new Set(["linear", "jira", "github", "azure-devops", "clickup"]);

function normalizeProvider(p) {
  if (!p) return "";
  const s = String(p)
    .toLowerCase()
    .trim()
    .replace(/_/g, "-");
  if (s === "azdo") return "azure-devops";
  return s;
}

export class TrackerFacade {
  constructor(config) {
    this.config = config;
    const fromEnv = normalizeProvider(process.env.TRACKER_PROVIDER);
    const fromCfg = normalizeProvider(config.tracker?.provider ?? "linear");
    this.provider =
      fromEnv && PROVIDERS.has(fromEnv) ? fromEnv : PROVIDERS.has(fromCfg) ? fromCfg : "linear";
    this.authError = "";
    this._linearClient = null;
    this.jira = null;
    this.github = null;
    this.azure = null;
    this.clickup = null;
    if (this.provider === "jira") this.jira = JiraCloudTracker.fromEnv(config);
    else if (this.provider === "github") this.github = GitHubIssuesTracker.fromEnv(config);
    else if (this.provider === "azure-devops") this.azure = AzureBoardsTracker.fromEnv(config);
    else if (this.provider === "clickup") this.clickup = ClickUpTracker.fromEnv(config);
  }

  _backend() {
    return this.jira ?? this.github ?? this.azure ?? this.clickup ?? null;
  }

  ensureAuth() {
    const b = this._backend();
    if (b) return true;
    if (this.provider === "jira") {
      this.authError =
        "Jira: set JIRA_HOST and JIRA_PAT (Bearer), or JIRA_EMAIL + JIRA_API_TOKEN (Basic auth for Cloud API tokens)";
      return false;
    }
    if (this.provider === "github") {
      this.authError =
        "GitHub Issues: set GITHUB_REPOSITORY=owner/repo (or GITHUB_ISSUES_OWNER + GITHUB_ISSUES_REPO) and GITHUB_TOKEN or GITHUB_ISSUES_TOKEN";
      return false;
    }
    if (this.provider === "azure-devops") {
      this.authError = "Azure DevOps: set AZURE_DEVOPS_ORG, AZURE_DEVOPS_PROJECT, AZURE_DEVOPS_PAT";
      return false;
    }
    if (this.provider === "clickup") {
      this.authError =
        "ClickUp: set CLICKUP_API_TOKEN and CLICKUP_LIST_ID (CLICKUP_TEAM_ID when using custom task ids)";
      return false;
    }
    const key = process.env[this.config.linear.apiKeyEnv];
    if (!key) {
      this.authError = `Missing ${this.config.linear.apiKeyEnv}`;
      return false;
    }
    if (!this._linearClient) {
      this._linearClient = linear.createLinearClient(key);
    }
    return true;
  }

  tryAuth() {
    return this.ensureAuth();
  }

  async getIssue(issueId) {
    const b = this._backend();
    if (b) return b.getIssue(issueId);
    return linear.getIssue(this._linearClient, issueId);
  }

  async getIssueByIdentifier(ref) {
    const b = this._backend();
    if (b) return b.getIssueByIdentifier(ref);
    return linear.getIssueByIdentifier(this._linearClient, ref);
  }

  async listIssues(filters, limit) {
    const b = this._backend();
    if (b) return b.listIssues(filters, limit);
    return linear.listIssues(this._linearClient, filters, limit);
  }

  async getNextIssueForRole(role, withoutBa) {
    const b = this._backend();
    if (b) return b.getNextIssueForRole(role, withoutBa);
    return linear.getNextIssueForRole(this._linearClient, role, withoutBa);
  }

  async updateIssueState(issueId, stateName) {
    const b = this._backend();
    if (b) return b.updateIssueState(issueId, stateName);
    return linear.updateIssueState(this._linearClient, issueId, stateName);
  }

  async addLabel(issueId, labelName, cfg) {
    const b = this._backend();
    if (b) return b.addLabel(issueId, labelName, cfg);
    return linear.addLabel(this._linearClient, issueId, labelName, cfg);
  }

  async removeLabel(issueId, labelName) {
    const b = this._backend();
    if (b) return b.removeLabel(issueId, labelName);
    return linear.removeLabel(this._linearClient, issueId, labelName);
  }

  async addComment(issueId, body) {
    const b = this._backend();
    if (b) return b.addComment(issueId, body);
    return linear.addComment(this._linearClient, issueId, body);
  }
}
