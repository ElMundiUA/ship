/**
 * Workspace settings page.
 *
 * Reads from `/v1/workspaces/{id}` and related endpoints. Mutations that
 * need forms POST to small route handlers so the page stays a server
 * component.
 */

import Link from "next/link";
import { redirect } from "next/navigation";
import { cookies } from "next/headers";

import { PageBody, PageHeader } from "@/components/app-shell";
import { ApiUnavailable } from "@/components/api-unavailable";
import { ConfigScopeCard } from "@/components/config-scope-card";
import { RepoRoutingPanel } from "@/components/repo-routing-panel";
import { AgentSecretsPanel } from "@/components/agent-secrets-panel";
import {
  IntegrationsWorkspaceBody,
  loadIntegrationsWorkspaceMode,
} from "@/components/integrations-workspace-body";
import { WorkspaceMembersPanelLoader } from "@/components/workspace-members-panel";
import { AgentRolesList } from "../agent-roles/agent-roles-list";
import { WorkspaceNameField } from "./workspace-name-field";
import {
  Badge,
  Card,
  CardHeader,
  type BadgeTone,
} from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  type ApiActivatedRepo,
  type ApiAgentRole,
  type ApiAgentRoleDefault,
  type ApiRepoConfig,
  getRepoConfig,
  isApiConfigured,
  listActivatedRepos,
  listArtifactRepos,
  listShipAgentRoleDefaults,
  listTokens,
  listWorkspaceAgentRoles,
} from "@/lib/api/client";
import type {
  ApiArtifactRepo,
  ApiTokenInfo,
  ApiWorkspace,
} from "@/lib/api/types";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace } from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

type Mode =
  | {
      source: "live";
      workspace: ApiWorkspace;
      allWorkspaces: ApiWorkspace[];
      activatedRepos: ApiActivatedRepo[];
      repoConfigs: Record<string, RepoConfigStatus>;
      repos: ApiArtifactRepo[];
      tokens: ApiTokenInfo[];
      agentRoleDefaults: ApiAgentRoleDefault[];
      agentRoleCustoms: ApiAgentRole[];
    }
  | { source: "unavailable"; errMsg: string };

type RepoConfigStatus =
  | { kind: "ready"; label: string; detail: string }
  | { kind: "legacy"; label: string; detail: string }
  | { kind: "missing"; label: string; detail: string }
  | { kind: "error"; label: string; detail: string };

function errorMessage(code: string): string {
  switch (code) {
    case "bad_input":
      return "Missing or invalid form input. Try again with a real workspace, layer, and URL.";
    case "bad_ttl":
      return "TTL must be a positive integer (days), or empty for non-expiring tokens.";
    case "invalid_url":
      return "That URL doesn’t look right. Use file://, https://, ssh://, or git@host:path style.";
    case "forbidden":
      return "You don’t have permission to change this workspace. Ask an admin to retry.";
    case "not_found":
      return "That artifact repo no longer exists — it may have been removed in another tab.";
    case "session_expired":
      return "Session expired — sign in again and retry.";
    case "bad_body":
      return "Ship could not build the seed request. Refresh and retry.";
    case "github_api_error":
      return "GitHub rejected the seed PR request. Check repo permissions and retry.";
    case "precondition_failed":
      return "Ship cannot open the seed PR yet because a setup precondition is missing.";
    case "slug_mismatch":
      return "Slug confirmation didn’t match. Type the workspace slug exactly as shown.";
    case "slug_taken":
      return "That slug is already used by another workspace in your org. Pick another.";
    case "bad_slug":
      return "Slug must be lowercase letters, digits, and dashes (3-64 chars, can't start or end with a dash).";
    case "api_unavailable":
      return "Backend is unreachable right now. Try again in a moment.";
    default:
      return `Couldn't save the change (${code}). Try again or refresh.`;
  }
}

// MCP-first console (ELS-304): five tabs, down from ten. Config folds
// into General; Connected code + Registries + Integrations merge into
// Connections; Agent roles folds into Agents & access; Workspaces +
// Danger merge into Workspace. Legacy tab ids alias to their new parent
// below so deep-links + the per-tab route files keep resolving.
const TABS = [
  { id: "general", label: "General" },
  { id: "connections", label: "Connections" },
  { id: "members", label: "Members" },
  { id: "api-keys", label: "Agents & access" },
  { id: "workspace", label: "Workspace" },
] as const;
type TabId = (typeof TABS)[number]["id"];
const TAB_IDS = TABS.map((t) => t.id);

// Old tab id → new parent. Keeps ``?tab=`` deep-links and the per-tab
// route files (settings/{old}/page.tsx) landing on the right merged tab.
const TAB_ALIASES: Record<string, TabId> = {
  tokens: "api-keys",
  repos: "connections",
  catalog: "general",
  config: "general",
  workspaces: "workspace",
  danger: "workspace",
  repositories: "connections",
  registries: "connections",
  integrations: "connections",
  "agent-roles": "api-keys",
};

function resolveTabId(value: string | null | undefined): TabId | null {
  if (!value) return null;
  if ((TAB_IDS as readonly string[]).includes(value)) return value as TabId;
  return TAB_ALIASES[value] ?? null;
}

async function load(
  searchParams: Record<string, string | string[] | undefined>,
): Promise<Mode> {
  if (!isApiConfigured()) {
    return { source: "unavailable", errMsg: "SHIP_API_URL is not set on this deployment." };
  }

  const token = await getCachedSessionToken();
  if (!token) redirect("/login?next=%2Fsettings&reason=session_expired");

  try {
    const ws = await getCachedWorkspaces();
    const resolved = await getResolvedWorkspaceId(searchParams, ws);
    if (ws.length > 1 && !resolved) {
      redirect("/?next=/settings");
    }

    const target = pickWorkspace(ws, resolved);
    // Every secondary endpoint here is best-effort: if any single one
    // hiccups (transient 500 during a backend rollout, stale build
    // returning 404, API throttle), we still want to render the rest
    // of the settings page rather than collapsing the whole shell into
    // an "unavailable" placeholder. The pre-rollout behaviour was that
    // listArtifactRepos returning 500 mid-deploy would tank the page,
    // hiding the General + Default-agent cards that don't depend on
    // that call at all.
    const activatedRepos = await listActivatedRepos(target.id, token).catch(
      () => [] as ApiActivatedRepo[],
    );
    const repoConfigs = Object.fromEntries(
      await Promise.all(
        activatedRepos.map(async (repo) => [
          repo.id,
          await loadRepoConfigStatus(target.id, repo.id, token),
        ]),
      ),
    );
    const repos = await listArtifactRepos(target.id, token).catch(
      () => [] as ApiArtifactRepo[],
    );
    const tokens = await listTokens(token).catch(() => [] as ApiTokenInfo[]);
    const [agentRoleDefaults, agentRoleCustoms] = await Promise.all([
      listShipAgentRoleDefaults(token).catch(
        () => [] as ApiAgentRoleDefault[],
      ),
      listWorkspaceAgentRoles(target.id, token).catch(
        () => [] as ApiAgentRole[],
      ),
    ]);
    return {
      source: "live",
      workspace: target,
      allWorkspaces: ws,
      activatedRepos,
      repoConfigs,
      repos,
      tokens,
      agentRoleDefaults,
      agentRoleCustoms,
    };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fsettings&reason=session_expired");
    }
    if (err instanceof ApiUnavailableError) {
      return { source: "unavailable", errMsg: err.message };
    }
    return {
      source: "unavailable",
      errMsg: err instanceof Error ? err.message : "Backend returned an error",
    };
  }
}

async function loadRepoConfigStatus(
  workspaceId: string,
  repoId: string,
  token: string,
): Promise<RepoConfigStatus> {
  try {
    return repoConfigStatus(await getRepoConfig(workspaceId, repoId, token));
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 404) {
      return {
        kind: "missing",
        label: "missing config",
        detail: "No .ship/config.yml found on the default branch.",
      };
    }
    return {
      kind: "error",
      label: "config unknown",
      detail: "Ship could not read this repo config right now.",
    };
  }
}

function repoConfigStatus(config: ApiRepoConfig): RepoConfigStatus {
  if (!config.exists) {
    return {
      kind: "missing",
      label: "missing config",
      detail: "No .ship/config.yml found on the default branch.",
    };
  }
  if (config.parse_error) {
    return {
      kind: "error",
      label: "parse error",
      detail: config.parse_error,
    };
  }
  if (config.parsed?.process) {
    return {
      kind: "ready",
      label: "FSM ready",
      detail: "This repo has a process: block in .ship/config.yml.",
    };
  }
  return {
    kind: "legacy",
    label: "legacy lanes",
    detail: "Config exists, but it has no process: block yet.",
  };
}

/**
 * Shared shell rendered by every per-tab route under /settings/{tab}.
 *
 * The `activeTab` prop wins over `?tab=` so each route file pins its own
 * tab. Legacy `?tab=` query strings are still recognised (some external
 * deep-links + the /members and /integrations redirect rules may still
 * land there) and aliased.
 */
export async function SettingsShell({
  activeTab: activeTabFromRoute,
  searchParams,
}: {
  // Accept any string (route files pin legacy ids like "config" /
  // "registries"); resolveTabId folds them into the 5 parents.
  activeTab?: string;
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as Record<
    string,
    string | string[] | undefined
  >;
  const data = await load(params);
  const errorCode = typeof params.error === "string" ? params.error : null;
  const renamedFlag = params.renamed === "1";
  const requestedTab = typeof params.tab === "string" ? params.tab : null;
  const activeTab: TabId =
    resolveTabId(activeTabFromRoute) ?? resolveTabId(requestedTab) ?? "general";
  const justMintedFlag =
    typeof params.just_minted === "string" && params.just_minted === "1";

  if (data.source === "unavailable") {
    return (
      <>
        <PageHeader kicker="settings" title="Workspace settings" />
        <PageBody>
          <ApiUnavailable scope="settings" details={data.errMsg} />
        </PageBody>
      </>
    );
  }

  // Read-and-clear the "just minted" cookie. We can't delete cookies in a
  // Server Component (only writeable in actions / route handlers), so we
  // settle for "show once and stop honouring the flag" by checking both
  // pieces — the cookie and the `?just_minted=1` flag from the redirect.
  let freshSecret: string | null = null;
  if (justMintedFlag) {
    const jar = await cookies();
    freshSecret = jar.get("ship_token_just_minted")?.value ?? null;
  }

  const {
    workspace,
    allWorkspaces,
    activatedRepos,
    repoConfigs,
    repos,
    tokens,
    agentRoleDefaults,
    agentRoleCustoms,
  } = data;
  const multiWs = allWorkspaces.length > 1;
  const settingsTabHref = (tabId: TabId) => {
    const qs = multiWs ? `?ws=${encodeURIComponent(workspace.id)}` : "";
    return `/settings/${tabId}${qs}`;
  };

  return (
    <>
      <PageHeader kicker="settings" title="Workspace settings" />
      <PageBody>
        {errorCode && (
          <div className="mb-5 rounded-xl border border-coral/30 bg-coral/[0.06] px-3 py-2 text-xs text-coral/95">
            {errorMessage(errorCode)}
          </div>
        )}
        {!errorCode && renamedFlag && (
          <div className="mb-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-3 py-2 text-xs text-aqua/95">
            Workspace renamed.
          </div>
        )}

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
          <nav className="space-y-1 text-sm">
            {TABS.map((tab) => {
              const current = tab.id === activeTab;
              return (
                <Link
                  key={tab.id}
                  href={settingsTabHref(tab.id)}
                  prefetch
                  className={
                    "block rounded-md px-3 py-1.5 transition " +
                    (current
                      ? "bg-white/[0.06] font-semibold text-white shadow-[inset_2px_0_0_theme(colors.aqua)]"
                      : "text-white/55 hover:bg-white/[0.04] hover:text-white")
                  }
                >
                  {tab.label}
                </Link>
              );
            })}
          </nav>

        <div className="space-y-6">
          {activeTab === "general" && (
            <div className="space-y-6">
              <Card>
                <CardHeader title="General" subtitle="Workspace identity" />
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <WorkspaceNameField workspace={workspace} />
                  <Field label="Slug (URL handle)" value={workspace.slug} mono readOnly />
                  <Field label="Workspace ID" value={workspace.id} mono readOnly />
                  <Field
                    label="Created"
                    value={new Date(workspace.created_at).toUTCString()}
                    readOnly
                  />
                </div>
              </Card>
              {/* ELS-236: declarative knobs ride the generic scope
                  renderer (GET/PUT /v1/.../config/{scope}) — the
                  bespoke form-POST BFF routes are retired. */}
              <ConfigScopeCard
                workspaceId={workspace.id}
                scope="agent.default_profile"
              />
              <ConfigScopeCard
                workspaceId={workspace.id}
                scope="agent.provider"
              />
              <AdvancedSurfacesCard workspaceId={workspace.id} multiWs={multiWs} />
              <ConfigPanel workspaceId={workspace.id} />
            </div>
          )}

          {activeTab === "workspace" && (
            <div className="space-y-6">
              <WorkspacesPanel
                current={workspace}
                memberships={allWorkspaces}
              />
              <DangerZone workspace={workspace} />
            </div>
          )}

          {activeTab === "connections" && (
            <div className="space-y-6">
              <RepositoriesPanel
                workspaceId={workspace.id}
                repositories={activatedRepos}
                repoConfigs={repoConfigs}
              />
              <RepoRoutingPanel
                workspaceId={workspace.id}
                repositories={activatedRepos}
              />
              <SettingsIntegrationsTab searchParams={params} />
              <Card>
              <CardHeader
                title="Registries"
                subtitle="Package and artifact sources Ship merges for this workspace. File paths work offline; git URLs sync in the background."
              />
              {repos.length === 0 ? (
                <p className="text-sm text-white/55">
                  No artifact repos registered yet. Add one below — the onboarding
                  wizard registers the project repo for you, but you can attach
                  additional shared catalogs here.
                </p>
              ) : (
                <table className="min-w-full text-sm">
                  <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
                    <tr>
                      <th className="px-3 py-2 text-left font-semibold">Kind</th>
                      <th className="px-3 py-2 text-left font-semibold">URL</th>
                      <th className="px-3 py-2 text-left font-semibold">Branch</th>
                      <th className="px-3 py-2 text-left font-semibold">Last sync</th>
                      <th className="px-3 py-2 text-left font-semibold">Status</th>
                      <th className="px-3 py-2 text-right font-semibold">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {repos.map((r) => (
                      <RepoRow key={r.id} repo={r} workspaceId={workspace.id} />
                    ))}
                  </tbody>
                </table>
              )}
              <AddRepoForm workspaceId={workspace.id} />
            </Card>
            </div>
          )}

          {activeTab === "api-keys" && (
            <div className="space-y-6">
              <AgentSecretsPanel workspaceId={workspace.id} repos={activatedRepos} />
              <TokensPanel
                workspaceId={workspace.id}
                tokens={tokens}
                freshSecret={freshSecret}
              />
              <Card>
                <CardHeader
                  title="Agent roles"
                  subtitle="Specialist prompts agents load when a routine fires. Ship ships read-only defaults; override one for this workspace, or clone a default into a custom slug. Most teams never touch this — defaults work."
                />
                <div className="mt-4">
                  <AgentRolesList
                    workspaceId={workspace.id}
                    defaults={agentRoleDefaults}
                    customs={agentRoleCustoms}
                  />
                </div>
              </Card>
            </div>
          )}

          {activeTab === "members" && (
            <WorkspaceMembersPanelLoader searchParams={searchParams} />
          )}
        </div>
        </div>
      </PageBody>
    </>
  );
}

function TokensPanel({
  workspaceId,
  tokens,
  freshSecret,
}: {
  workspaceId: string;
  tokens: ApiTokenInfo[];
  freshSecret: string | null;
}) {
  return (
    <>
      {freshSecret && (
        <Card className="border-aqua/40 bg-aqua/[0.06]">
          <CardHeader
            title="API key created"
            subtitle="Copy it now — the secret will never be shown again."
          />
          <div className="rounded-xl border border-aqua/30 bg-ink/60 p-3">
            <code className="block break-all font-mono text-xs text-aqua/95">
              {freshSecret}
            </code>
          </div>
          <p className="mt-2 text-[11px] text-white/60">
            Use this as the bearer token for the CLI:{" "}
            <code className="font-mono">ship --token={freshSecret.slice(0, 16)}…</code>
          </p>
        </Card>
      )}
      <Card>
        <CardHeader
          title="Agents & access"
          subtitle="PATs for everything that talks to Ship: your operator agent over MCP, the CLI, and CI. Name keys by purpose (operator agent · ci · e2e) — the secret is shown only once at creation."
        />
        {tokens.length === 0 ? (
          <p className="mb-4 text-sm text-white/55">
            No PATs yet. Mint one below to attach your agent over MCP
            (<code className="font-mono">claude mcp add ship …</code>) or to
            drive <code className="font-mono">shipctl</code> from CI or your
            laptop.
          </p>
        ) : (
          <table className="min-w-full text-sm">
            <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
              <tr>
                <th className="px-3 py-2 text-left font-semibold">Name</th>
                <th className="px-3 py-2 text-left font-semibold">Scope</th>
                <th className="px-3 py-2 text-left font-semibold">Created</th>
                <th className="px-3 py-2 text-left font-semibold">Last used</th>
                <th className="px-3 py-2 text-left font-semibold">Expires</th>
                <th className="px-3 py-2 text-right font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody>
              {tokens.map((t) => (
                <TokenRow key={t.id} token={t} />
              ))}
            </tbody>
          </table>
        )}
        <details className="mt-4 rounded-xl border border-white/10 bg-white/[0.02] p-3 text-sm">
          <summary className="cursor-pointer text-[11px] font-bold uppercase tracking-widest text-white/65 hover:text-white">
            + Create API key
          </summary>
          <form
            action="/api/tokens/mint"
            method="POST"
            className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-[minmax(0,1fr)_8rem_8rem_auto]"
          >
            <input
              type="hidden"
              name="ws"
              value={workspaceId}
              suppressHydrationWarning
            />
            <label className="block">
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
                Name
              </span>
              <input
                name="name"
                type="text"
                required
                placeholder="ci-push  ·  laptop-cli  ·  bot-runner"
                suppressHydrationWarning
                className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
                TTL (days)
              </span>
              <input
                name="ttl"
                type="number"
                min={1}
                max={730}
                placeholder="never"
                suppressHydrationWarning
                className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
              />
            </label>
            <label className="flex items-end text-[11px] text-white/55">
              <span>Workspace-scoped</span>
            </label>
            <div className="flex items-end">
              <button
                type="submit"
                className="w-full rounded-full bg-aqua/80 px-3 py-1.5 text-xs font-bold text-ink transition hover:bg-aqua md:w-auto"
              >
                Mint
              </button>
            </div>
          </form>
          <p className="mt-2 text-[11px] text-white/55">
            Workspace-scoped keys can only act on this workspace. Leave TTL
            empty for a non-expiring key (revoke it manually when no longer
            needed).
          </p>
        </details>
      </Card>
    </>
  );
}

function RepositoriesPanel({
  workspaceId,
  repositories,
  repoConfigs,
}: {
  workspaceId: string;
  repositories: ApiActivatedRepo[];
  repoConfigs: Record<string, RepoConfigStatus>;
}) {
  return (
    <Card>
      <CardHeader
        title="Connected code"
        subtitle="Git repositories Ship is wired into. Re-seed the bundle when the install PR goes stale."
      />
      {repositories.length === 0 ? (
        <div className="rounded-xl border border-dashed border-white/10 bg-white/[0.02] p-4 text-sm text-white/55">
          No repositories are activated for this workspace yet. Use the setup
          flow to connect GitHub and pick at least one repository.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
              <tr>
                <th className="px-3 py-2 text-left font-semibold">Repository</th>
                <th className="px-3 py-2 text-left font-semibold">Provider</th>
                <th className="px-3 py-2 text-left font-semibold">Seed bundle</th>
                <th className="px-3 py-2 text-left font-semibold">Config</th>
                <th className="px-3 py-2 text-left font-semibold">Activated</th>
                <th className="px-3 py-2 text-right font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody>
              {repositories.map((repo) => (
                <RepositoryRow
                  key={repo.id}
                  workspaceId={workspaceId}
                  repo={repo}
                  configStatus={repoConfigs[repo.id] ?? unknownRepoConfigStatus}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-4 rounded-xl border border-aqua/20 bg-aqua/[0.04] px-3 py-2.5 text-[11px] leading-relaxed text-aqua/85">
        <strong>FSM config.</strong> Repositories without a{" "}
        <code className="font-mono">process:</code> block can open a fresh seed PR
        directly from this table. The PR includes the current Ship bundle and
        updates <code className="font-mono">.ship/config.yml</code> with the
        process template.
      </div>
      <div className="mt-4">
        <a
          href={`/onboarding?step=repos&ws=${encodeURIComponent(workspaceId)}`}
          className="btn-primary"
        >
          + Add another repository
        </a>
        <p className="mt-2 text-[11px] text-white/45">
          Opens the connect flow so you can pick an additional GitHub repository.
        </p>
      </div>
    </Card>
  );
}

function RepositoryRow({
  workspaceId,
  repo,
  configStatus,
}: {
  workspaceId: string;
  repo: ApiActivatedRepo;
  configStatus: RepoConfigStatus;
}) {
  const installed = repo.installed_bundle_version;
  const current = repo.current_bundle_version;
  const stale = installed == null || compareBundleVersions(installed, current) < 0;
  return (
    <tr className="border-t border-white/5">
      <td className="px-3 py-2.5 align-top">
        <a
          href={repo.html_url}
          target="_blank"
          rel="noreferrer"
          className="font-semibold text-white hover:text-aqua"
        >
          {repo.full_name}
        </a>
        <div className="mt-1 text-[10px] text-white/45">
          {repo.private ? "private" : "public"} · {repo.default_branch}
        </div>
      </td>
      <td className="px-3 py-2.5 align-top">
        <Badge tone="neutral">{repo.provider}</Badge>
      </td>
      <td className="px-3 py-2.5 align-top">
        <Badge tone={stale ? "warn" : "ok"} dot>
          {stale ? "reseed needed" : "current"}
        </Badge>
        <div className="mt-1 text-[10px] text-white/45">
          installed {installed ?? "never"} · current {current}
        </div>
      </td>
      <td className="px-3 py-2.5 align-top">
        <Badge tone={repoConfigTone(configStatus)} dot>
          {configStatus.label}
        </Badge>
        <div className="mt-1 max-w-[18rem] text-[10px] text-white/45">
          {configStatus.detail}
        </div>
      </td>
      <td className="px-3 py-2.5 align-top text-xs text-white/60">
        {repo.activated_at ? new Date(repo.activated_at).toUTCString() : "unknown"}
      </td>
      <td className="space-y-2 px-3 py-2.5 text-right align-top">
        <form action="/api/settings/repositories/reseed" method="post">
          <input type="hidden" name="workspaceId" value={workspaceId} />
          <input type="hidden" name="repoId" value={repo.id} />
          {/*
            ``include_fsm`` keys off the *config* status, not the bundle
            staleness. When the repo already has a ``process:`` block
            (FSM ready), the reseed PR refreshes the bundle (knowledge
            starters, agent rule files, scheduled-trigger workflow) but
            **leaves the existing process block alone** — otherwise an
            "update Ship version" click would silently rewrite the
            operator's tailored FSM. Repos that have no FSM block yet
            (missing / legacy config) get the full seed including the
            process template, same as the Full setup wizard.
          */}
          <input
            type="hidden"
            name="include_fsm"
            value={configStatus.kind === "ready" ? "false" : "true"}
          />
          <button
            type="submit"
            disabled={!stale}
            className="inline-flex rounded-full border border-aqua/30 bg-aqua/10 px-3 py-1 text-[10px] font-bold uppercase tracking-widest text-aqua transition hover:bg-aqua/20 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-white/30"
          >
            Update Ship version
          </button>
        </form>
        <a
          href={`/onboarding?step=confirm&ws=${encodeURIComponent(workspaceId)}`}
          className="inline-flex text-[10px] font-semibold uppercase tracking-widest text-white/40 hover:text-white/70"
        >
          Full setup wizard
        </a>
        <DisconnectRepoForm workspaceId={workspaceId} repo={repo} />
      </td>
    </tr>
  );
}

/**
 * Inline disconnect affordance. Backend wires
 * ``DELETE /v1/workspaces/{ws}/repos/{id}`` (deletes the row, every
 * Pipeline bound to it, and every Run under those pipelines —
 * cascades fire on the model side). The route handler at
 * ``/api/dashboard/disconnect-repo`` requires a ``confirm=disconnect``
 * field so a stray click can't nuke state, and redirects back to ``/``
 * with a toast on success.
 *
 * We deliberately keep this inline (collapsed ``<details>``) rather
 * than punting the user to ``/r/<owner>/<repo>/settings``: the
 * per-repo settings page exists, but operators looking at the
 * workspace repo list don't think to drill into a repo's URL just
 * to remove it from Ship.
 *
 * GitHub-side cleanup (App's ``selected_repositories``, the workflow
 * YAMLs the install PR added) stays the operator's job — Ship never
 * touches github.com on disconnect.
 */
function DisconnectRepoForm({
  workspaceId,
  repo,
}: {
  workspaceId: string;
  repo: ApiActivatedRepo;
}) {
  return (
    <details className="inline-block">
      <summary className="cursor-pointer list-none text-[10px] font-semibold uppercase tracking-widest text-coral/70 hover:text-coral [&::-webkit-details-marker]:hidden">
        Disconnect →
      </summary>
      <form
        action="/api/dashboard/disconnect-repo"
        method="POST"
        className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-coral/30 bg-coral/[0.05] p-2"
      >
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="repo_id" value={repo.id} />
        <input
          type="text"
          name="confirm"
          required
          autoComplete="off"
          pattern="disconnect"
          placeholder='type "disconnect"'
          className="w-32 rounded-md border border-coral/40 bg-black/30 px-2 py-1 font-mono text-[11px] text-white placeholder:text-white/35 focus:border-coral/80 focus:outline-none"
        />
        <button
          type="submit"
          className="rounded-full border border-coral/60 bg-coral/15 px-3 py-1 text-[10px] font-bold uppercase tracking-widest text-coral hover:bg-coral/25"
        >
          Confirm disconnect
        </button>
        <p className="basis-full text-[10px] leading-snug text-white/45">
          Removes Ship&apos;s pipelines + run history for this repo. Doesn&apos;t
          touch the repo on GitHub.
        </p>
      </form>
    </details>
  );
}

const unknownRepoConfigStatus: RepoConfigStatus = {
  kind: "error",
  label: "config unknown",
  detail: "Ship did not load this repository config.",
};

function repoConfigTone(status: RepoConfigStatus): BadgeTone {
  switch (status.kind) {
    case "ready":
      return "ok";
    case "legacy":
      return "warn";
    case "missing":
      return "info";
    case "error":
      return "err";
  }
}

function compareBundleVersions(left: string, right: string): number {
  const a = left.split(".").map((part) => Number.parseInt(part, 10) || 0);
  const b = right.split(".").map((part) => Number.parseInt(part, 10) || 0);
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i += 1) {
    const diff = (a[i] ?? 0) - (b[i] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
}

function TokenRow({ token }: { token: ApiTokenInfo }) {
  const created = new Date(token.created_at).toUTCString();
  const lastUsed = token.last_used_at
    ? new Date(token.last_used_at).toUTCString()
    : "never";
  const expires = token.expires_at
    ? new Date(token.expires_at).toUTCString()
    : "never";
  const scope = token.workspace_id ? "workspace" : "user";
  return (
    <tr className="border-t border-white/5">
      <td className="px-3 py-2.5 align-top">
        <div className="font-semibold text-white">{token.name}</div>
        <div className="text-[10px] font-mono text-white/45">{token.prefix}…</div>
      </td>
      <td className="px-3 py-2.5 align-top">
        <Badge tone={scope === "workspace" ? "workspace" : "neutral"}>{scope}</Badge>
      </td>
      <td className="px-3 py-2.5 align-top text-xs text-white/65">{created}</td>
      <td className="px-3 py-2.5 align-top text-xs text-white/65">{lastUsed}</td>
      <td className="px-3 py-2.5 align-top text-xs text-white/65">{expires}</td>
      <td className="px-3 py-2.5 align-top text-right">
        <form action="/api/tokens/revoke" method="POST" className="inline-block">
          <input type="hidden" name="token" value={token.id} suppressHydrationWarning />
          <button
            type="submit"
            className="rounded-full border border-coral/30 bg-coral/10 px-2.5 py-1 text-[10px] font-bold uppercase tracking-widest text-coral/85 transition hover:bg-coral/20"
          >
            Revoke
          </button>
        </form>
      </td>
    </tr>
  );
}

/**
 * MCP-first rework (ELS-289): Process / Knowledge / Policies left the
 * nav rail but stay fully routable — this card is their wayfinding.
 */
function AdvancedSurfacesCard({
  workspaceId,
  multiWs,
}: {
  workspaceId: string;
  multiWs: boolean;
}) {
  const qs = multiWs ? `?ws=${encodeURIComponent(workspaceId)}` : "";
  const links = [
    {
      href: `/process${qs}`,
      label: "Process editor",
      hint: "per-stage agents, routines, FSM states",
    },
    {
      href: `/knowledge${qs}`,
      label: "Knowledge",
      hint: "buckets, corpus, importers",
    },
    {
      href: `/settings/policy${qs}`,
      label: "Policies",
      hint: "guardrails agents must follow",
    },
  ];
  return (
    <Card data-testid="advanced-surfaces">
      <CardHeader
        title="Advanced surfaces"
        subtitle="Pages that left the navigation rail in the MCP-first rework. Still fully functional — most operators drive these through their agent instead."
      />
      <ul className="space-y-2 text-sm">
        {links.map((l) => (
          <li key={l.href}>
            <Link href={l.href} className="font-semibold text-aqua hover:underline">
              {l.label} →
            </Link>{" "}
            <span className="text-white/45">{l.hint}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

async function ConfigPanel({ workspaceId }: { workspaceId: string }) {
  // Server-rendered list (config_help-without-scope), per-row card is
  // a client component that does its own GET + PUT cycle. Keeps the
  // server boundary thin — no client-side scope discovery, but the
  // form-renderer stays interactive.
  const token = await getCachedSessionToken();
  if (!token) {
    return (
      <Card>
        <CardHeader title="Workspace configuration" />
        <p className="text-sm text-white/55">Sign in to view scopes.</p>
      </Card>
    );
  }
  let rows: { slug: string; description: string }[] = [];
  let errorMsg: string | null = null;
  try {
    const { listConfigScopes } = await import("@/lib/api/client");
    rows = await listConfigScopes(workspaceId, token);
  } catch (err) {
    errorMsg =
      err instanceof Error ? err.message : "Couldn't load config scopes.";
  }
  return (
    <Card>
      <CardHeader
        title="Workspace configuration"
        subtitle="Every per-workspace setting under one roof. Adding a new ConfigScope server-side picks up here automatically — no FE change. Mutating scopes are admin-gated and audited under their canonical action name."
      />
      {errorMsg ? (
        <p className="rounded-md border border-coral/30 bg-coral/[0.04] p-3 text-xs text-coral/85">
          {errorMsg}
        </p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-white/55">No scopes registered.</p>
      ) : (
        <div className="space-y-4">
          {rows.map((row) => (
            <ConfigScopeCard
              key={row.slug}
              workspaceId={workspaceId}
              scope={row.slug}
            />
          ))}
        </div>
      )}
    </Card>
  );
}


function WorkspacesPanel({
  current,
  memberships,
}: {
  current: ApiWorkspace;
  memberships: ApiWorkspace[];
}) {
  // ``memberships`` already includes the current workspace; surface the
  // others first so the operator scans visible peers without re-reading
  // the row right above.
  const peers = memberships.filter((w) => w.id !== current.id);
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Your workspaces"
          subtitle="Every workspace you're a member of. The active one stays in your URL (?ws=…) and a long-lived cookie — switching is one click."
        />
        {peers.length === 0 ? (
          <p className="text-sm text-white/55">
            You&apos;re only a member of this workspace. Create another below.
          </p>
        ) : (
          <ul className="divide-y divide-white/[0.06]">
            {peers.map((w) => (
              <li
                key={w.id}
                className="flex items-center justify-between gap-3 py-2.5"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm text-white/90">{w.name}</div>
                  <div className="truncate text-[11px] text-white/45">
                    <code className="font-mono">{w.slug}</code> ·{" "}
                    {new Date(w.created_at).toUTCString()}
                  </div>
                </div>
                <Link
                  href={`/?ws=${encodeURIComponent(w.id)}`}
                  className="rounded-full border border-white/15 bg-white/[0.04] px-3 py-1 text-[11px] font-semibold text-white/85 transition hover:border-aqua/40 hover:text-aqua"
                >
                  Switch
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card id="create-workspace">
        <CardHeader
          title="Create a new workspace"
          subtitle="Spins up an independent tenant in your personal org: empty knowledge bucket, default policies, you as owner. No data shared with this workspace."
        />
        <form
          action="/api/settings/workspace/create"
          method="POST"
          className="grid grid-cols-1 gap-4 md:grid-cols-2"
        >
          <label className="block">
            <div className="mb-1 text-[10px] font-bold uppercase tracking-widest text-white/45">
              Name
            </div>
            <input
              name="name"
              required
              maxLength={200}
              placeholder="e.g. Acme experiments"
              className="w-full rounded-lg border border-white/10 bg-white/[0.06] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
              suppressHydrationWarning
            />
          </label>
          <label className="block">
            <div className="mb-1 text-[10px] font-bold uppercase tracking-widest text-white/45">
              Slug (URL handle)
            </div>
            <input
              name="slug"
              required
              maxLength={64}
              pattern="^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
              placeholder="acme-experiments"
              className="w-full rounded-lg border border-white/10 bg-white/[0.06] px-3 py-2 font-mono text-sm text-white outline-none focus:border-aqua/40"
              suppressHydrationWarning
            />
            <div className="mt-1 text-[11px] text-white/45">
              Lowercase letters, digits, dashes. Immutable after create.
            </div>
          </label>
          <div className="md:col-span-2 flex items-center justify-end">
            <button type="submit" className="btn-primary">
              Create workspace
            </button>
          </div>
        </form>
      </Card>
    </div>
  );
}

function DangerZone({ workspace }: { workspace: ApiWorkspace }) {
  return (
    <Card>
      <CardHeader
        title="Danger zone"
        subtitle="Permanent. Removes members, repos, integrations, telemetry, and audit. Git remotes untouched."
      />
      <form
        action="/api/settings/workspace/delete"
        method="POST"
        className="space-y-3 rounded-xl border border-coral/20 bg-coral/[0.04] p-3"
      >
        <input type="hidden" name="ws" value={workspace.id} suppressHydrationWarning />
        <div className="text-sm text-white/85">
          Type the workspace slug{" "}
          <code className="font-mono text-coral/95">{workspace.slug}</code> to
          confirm.
        </div>
        <input
          name="slug_confirmation"
          type="text"
          required
          placeholder={workspace.slug}
          autoComplete="off"
          suppressHydrationWarning
          className="w-full rounded border border-coral/30 bg-ink/60 px-2 py-1.5 font-mono text-sm text-white outline-none focus:border-coral"
        />
        <div className="flex items-center justify-end">
          <button
            type="submit"
            className="rounded-full border border-coral/40 bg-coral/15 px-3 py-1.5 text-xs font-bold text-coral transition hover:bg-coral/25"
          >
            Delete workspace
          </button>
        </div>
      </form>
    </Card>
  );
}

function Field({
  label,
  value,
  mono,
  readOnly,
}: {
  label: string;
  value: string;
  mono?: boolean;
  readOnly?: boolean;
}) {
  return (
    <label className="block">
      <div className="mb-1 text-[10px] font-bold uppercase tracking-widest text-white/45">
        {label}
      </div>
      {readOnly ? (
        <div
          className={
            "rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-sm text-white/75 " +
            (mono ? "font-mono" : "")
          }
        >
          {value}
        </div>
      ) : (
        <input
          defaultValue={value}
          className={
            "w-full rounded-lg border border-white/10 bg-white/[0.06] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40 " +
            (mono ? "font-mono" : "")
          }
        />
      )}
    </label>
  );
}

async function SettingsIntegrationsTab({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>;
}) {
  const data = await loadIntegrationsWorkspaceMode(searchParams);
  if (data.source === "mock") {
    return (
      <p className="text-sm text-white/55">Integrations aren&apos;t available: {data.reason}</p>
    );
  }
  return <IntegrationsWorkspaceBody data={data} />;
}

function RepoRow({
  repo,
  workspaceId,
}: {
  repo: ApiArtifactRepo;
  workspaceId: string;
}) {
  const lastSync =
    repo.last_sync_at == null
      ? "never"
      : new Date(repo.last_sync_at).toUTCString();
  const status: "ok" | "warn" | "error" =
    repo.last_sync_error != null
      ? "error"
      : repo.last_sync_at == null
      ? "warn"
      : "ok";
  return (
    <tr className="border-t border-white/5">
      <td className="px-3 py-2.5 align-top">
        <Badge tone={repo.kind === "project" ? "project" : "workspace"}>
          {repo.kind}
        </Badge>
      </td>
      <td className="px-3 py-2.5 align-top text-xs">
        <code className="font-mono text-white/85">{repo.url}</code>
        {repo.last_sync_error && (
          <div className="mt-1 text-[11px] text-coral/85">
            {repo.last_sync_error}
          </div>
        )}
      </td>
      <td className="px-3 py-2.5 align-top text-xs text-white/65">
        {repo.default_branch || "—"}
      </td>
      <td className="px-3 py-2.5 align-top text-xs text-white/60">
        {lastSync}
        {repo.last_sync_sha && (
          <span className="ml-1 font-mono text-white/45">
            · {repo.last_sync_sha.slice(0, 7)}
          </span>
        )}
      </td>
      <td className="px-3 py-2.5 align-top">
        <Badge tone={status === "ok" ? "ok" : status === "error" ? "warn" : "neutral"} dot>
          {status === "ok" ? "synced" : status === "error" ? "sync error" : "pending"}
        </Badge>
      </td>
      <td className="px-3 py-2.5 align-top text-right">
        <form
          action="/api/settings/artifact-repos/delete"
          method="POST"
          className="inline-block"
        >
          <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
          <input type="hidden" name="repo" value={repo.id} suppressHydrationWarning />
          <button
            type="submit"
            className="rounded-full border border-coral/30 bg-coral/10 px-2.5 py-1 text-[10px] font-bold uppercase tracking-widest text-coral/85 transition hover:bg-coral/20"
          >
            Remove
          </button>
        </form>
      </td>
    </tr>
  );
}

function AddRepoForm({ workspaceId }: { workspaceId: string }) {
  // Collapsed by default so the table reads cleanly when full. <details>
  // keeps us in server-component land — no React state, no client JS.
  return (
    <details className="mt-4 rounded-xl border border-white/10 bg-white/[0.02] p-3 text-sm">
      <summary className="cursor-pointer text-[11px] font-bold uppercase tracking-widest text-white/65 hover:text-white">
        + Add repo
      </summary>
      <form
        action="/api/settings/artifact-repos/create"
        method="POST"
        className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-[7rem_minmax(0,1fr)_8rem_auto]"
      >
        <input
          type="hidden"
          name="ws"
          value={workspaceId}
          suppressHydrationWarning
        />
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            Layer
          </span>
          <select
            name="kind"
            defaultValue="workspace"
            suppressHydrationWarning
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs text-white outline-none focus:border-aqua/40"
          >
            <option value="workspace">workspace</option>
            <option value="project">project</option>
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            URL
          </span>
          <input
            name="url"
            type="text"
            required
            placeholder="file:///srv/catalogs/aurora  ·  https://github.com/me/catalog"
            suppressHydrationWarning
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 font-mono text-xs text-white outline-none focus:border-aqua/40"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            Branch
          </span>
          <input
            name="default_branch"
            type="text"
            defaultValue="main"
            suppressHydrationWarning
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs text-white outline-none focus:border-aqua/40"
          />
        </label>
        <div className="flex items-end">
          <button
            type="submit"
            className="w-full rounded-full bg-aqua/80 px-3 py-1.5 text-xs font-bold text-ink transition hover:bg-aqua md:w-auto"
          >
            Register
          </button>
        </div>
      </form>
      <p className="mt-2 text-[11px] text-white/55">
        <code className="font-mono">file://</code> paths are read inline. Git
        URLs (https/ssh) are accepted but won&rsquo;t contribute artifacts
        until the upcoming GitHub App integration replaces the legacy
        sync-worker flow.
      </p>
    </details>
  );
}

