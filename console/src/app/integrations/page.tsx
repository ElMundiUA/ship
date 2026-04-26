/**
 * Integrations & secrets page.
 *
 * Reads from `/v1/workspaces/{id}/integrations` when a live session + workspace
 * exist; otherwise falls back to mock fixtures so the marketing-style preview
 * still has something to look at. Connecting / editing happens via native form
 * POSTs to `/api/integrations/upsert` (and DELETE via `/api/integrations/delete`)
 * to keep us off the cookie-eating Server Action codepath.
 */

import { AppShell } from "@/components/app-shell";
import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  LiveBanner,
  MockBanner,
} from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listIntegrations,
  listNativeBindings,
  listNativeIntegrations,
  listNativeRepoResources,
  listWorkspaces,
  type ApiNativeBinding,
  type ApiNativeIntegration,
  type ApiNativeResource,
} from "@/lib/api/client";
import type { ApiIntegration, ApiWorkspace } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";
import { integrations as mockIntegrations, workspaces as mockWorkspaces } from "@/lib/mock/cloud";
import {
  parseWorkspaceIdParam,
  pickWorkspace,
  toAppShellWorkspaces,
} from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

type Mode =
  | {
      source: "live";
      workspace: ApiWorkspace;
      allWorkspaces: ApiWorkspace[];
      rows: ApiIntegration[];
      nativeRows: ApiNativeIntegration[];
      nativeRepoResources: Record<string, ApiNativeResource[]>;
      nativeBindings: Record<string, ApiNativeBinding[]>;
    }
  | { source: "mock"; reason: string };

const CATALOG: {
  id: string;
  name: string;
  group: string;
  body: string;
  fields: { name: string; label: string; placeholder?: string }[];
  secretLabel: string;
}[] = [
  {
    id: "jira",
    name: "Jira",
    group: "Tracker",
    body: "Same contract as Linear; supports Cloud + Data Center.",
    fields: [
      { name: "host", label: "Host", placeholder: "yourorg.atlassian.net" },
      { name: "project", label: "Project key", placeholder: "ENG" },
    ],
    secretLabel: "API token",
  },
  {
    id: "notion",
    name: "Notion",
    group: "Tracker",
    body: "Pages-as-tickets. Mirrors approved retro/daily action items into a database.",
    fields: [
      { name: "database_id", label: "Database ID", placeholder: "32-char hex" },
    ],
    secretLabel: "Internal integration token (secret_… or ntn_…)",
  },
  {
    id: "github",
    name: "GitHub",
    group: "Source",
    body: "PR review handles for catalog merges; webhook auth.",
    fields: [{ name: "org", label: "Org", placeholder: "your-org" }],
    secretLabel: "Personal access token (ghp_…)",
  },
  {
    id: "slack",
    name: "Slack",
    group: "Comms",
    body: "Daily digest, retro summary, and high-severity action items.",
    fields: [{ name: "channel", label: "Channel", placeholder: "#ship-daily" }],
    secretLabel: "Bot token (xoxb-…)",
  },
  {
    id: "teams",
    name: "Microsoft Teams",
    group: "Comms",
    body: "Same as Slack: daily digest + alerts to a channel.",
    fields: [{ name: "team_id", label: "Team ID" }],
    secretLabel: "Webhook URL or app password",
  },
  {
    id: "otel",
    name: "OpenTelemetry",
    group: "Telemetry",
    body: "OTLP exporter for events; choose Honeycomb / Datadog / Tempo etc.",
    fields: [{ name: "endpoint", label: "Endpoint", placeholder: "https://otlp…" }],
    secretLabel: "Bearer token / API key",
  },
  {
    id: "webhook",
    name: "Custom webhook",
    group: "Telemetry",
    body: "Fire-and-forget POST per event class with HMAC signature.",
    fields: [{ name: "url", label: "Endpoint URL", placeholder: "https://…" }],
    secretLabel: "HMAC signing secret",
  },
  {
    id: "s3-export",
    name: "S3 export",
    group: "Telemetry",
    body: "Hourly JSONL drop into your bucket; works for offline analytics.",
    fields: [
      { name: "bucket", label: "Bucket" },
      { name: "region", label: "Region" },
    ],
    secretLabel: "Access key secret",
  },
];

const CATALOG_BY_ID: Record<string, (typeof CATALOG)[number]> = Object.fromEntries(
  CATALOG.map((c) => [c.id, c]),
);

const NATIVE_CATALOG = [
  {
    id: "linear",
    name: "Linear",
    group: "Tracker",
    body:
      "Connect Linear with OAuth so Ship can create, list, transition, and comment on issues.",
  },
  {
    id: "atlassian",
    name: "Jira + Confluence",
    group: "Tracker + Knowledge",
    body:
      "Connect one Atlassian Cloud site. Jira handles corporate tickets; Confluence feeds curated knowledge buckets.",
  },
  {
    id: "azure_devops",
    name: "Azure DevOps",
    group: "Code host + Orchestrator",
    body:
      "Connect a corporate Azure DevOps organization with a PAT for Repos and Pipelines access.",
  },
  {
    id: "gitlab",
    name: "GitLab",
    group: "Code host + Orchestrator",
    body:
      "Connect GitLab.com or a self-hosted GitLab instance with a PAT for repos and CI status.",
  },
];

async function load(wsParam: string | undefined): Promise<Mode> {
  if (!isApiConfigured()) return { source: "mock", reason: "SHIP_API_URL not set" };
  const token = await getSessionToken();
  if (!token) return { source: "mock", reason: "Sign in to manage real integrations" };
  try {
    const ws = await listWorkspaces(token);
    if (ws.length === 0)
      return { source: "mock", reason: "Create a workspace first to wire integrations" };
    const target = pickWorkspace(ws, wsParam);
    const [rows, nativeRows] = await Promise.all([
      listIntegrations(target.id, token),
      listNativeIntegrations(target.id, token),
    ]);
    const codeHostRows = nativeRows.filter(
      (row) =>
        !row.disabled_at &&
        (row.provider === "azure_devops" || row.provider === "gitlab"),
    );
    const resourcePairs = await Promise.all(
      codeHostRows.map(async (row) => {
        try {
          const [resources, bindings] = await Promise.all([
            listNativeRepoResources(target.id, row.id, token),
            listNativeBindings(target.id, row.id, token),
          ]);
          return [row.id, resources, bindings] as const;
        } catch {
          return [
            row.id,
            [] as ApiNativeResource[],
            [] as ApiNativeBinding[],
          ] as const;
        }
      }),
    );
    const nativeRepoResources: Record<string, ApiNativeResource[]> = {};
    const nativeBindings: Record<string, ApiNativeBinding[]> = {};
    for (const [id, resources, bindings] of resourcePairs) {
      nativeRepoResources[id] = resources;
      nativeBindings[id] = bindings;
    }
    return {
      source: "live",
      workspace: target,
      allWorkspaces: ws,
      rows,
      nativeRows,
      nativeRepoResources,
      nativeBindings,
    };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return { source: "mock", reason: "Session expired — sign in again" };
    }
    if (err instanceof ApiUnavailableError) {
      return { source: "mock", reason: "Backend unreachable" };
    }
    return { source: "mock", reason: "Backend returned an error" };
  }
}

export default async function IntegrationsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = (await (searchParams ?? Promise.resolve({}))) as Record<
    string,
    string | string[] | undefined
  >;
  const wsParam = parseWorkspaceIdParam(params.ws);
  const data = await load(wsParam);

  if (data.source === "mock") {
    return <MockView reason={data.reason} />;
  }

  const {
    workspace,
    allWorkspaces,
    rows,
    nativeRows,
    nativeRepoResources,
    nativeBindings,
  } = data;
  const connectedNativeProviders = new Set(
    nativeRows.filter((r) => !r.disabled_at).map((r) => r.provider),
  );
  const visibleRows = rows.filter(
    (r) => !isShadowedByNativeProvider(r.kind, connectedNativeProviders),
  );
  const connectedIds = new Set(visibleRows.map((r) => r.kind));
  const available = CATALOG.filter((c) => !connectedIds.has(c.id));
  const nativeAvailable = NATIVE_CATALOG.filter(
    (c) => !connectedNativeProviders.has(c.id),
  );

  return (
    <AppShell
      kicker={`${workspace.name} · integrations`}
      title="Integrations"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      allWorkspaces={toAppShellWorkspaces(allWorkspaces)}
      actions={<ButtonPrimary>+ Custom integration</ButtonPrimary>}
    >
      <LiveBanner workspace={workspace.slug} />

      <Card className="mb-8">
        <CardHeader
          title="Connected"
          subtitle="Configured for this workspace · secrets are encrypted at rest"
        />
        {visibleRows.length === 0 ? (
          <p className="text-sm text-white/55">
            Nothing connected yet. Pick one below to drop in your first secret.
          </p>
        ) : (
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {visibleRows.map((i) => {
              const meta = CATALOG_BY_ID[i.kind];
              return (
                <li
                  key={i.id}
                  className="flex items-start gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3"
                >
                  <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-white/15 to-white/[0.02] text-xs font-bold uppercase text-white/85">
                    {i.kind.slice(0, 2)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-semibold text-white">
                        {meta?.name ?? i.kind}
                      </span>
                      <Badge
                        tone={
                          i.status === "ok"
                            ? "ok"
                            : i.status === "error"
                            ? "warn"
                            : "neutral"
                        }
                        dot
                      >
                        {i.status}
                      </Badge>
                    </div>
                    <div className="mt-0.5 text-[11px] text-white/55">
                      {summariseConfig(i.config)}
                      {i.has_secret ? " · secret stored" : " · no secret"}
                    </div>
                    <HealthLine
                      lastAt={i.last_health_at}
                      lastError={i.last_health_error}
                      status={i.status}
                    />
                    <div className="mt-2 flex flex-wrap items-center gap-3">
                      {i.kind === "notion" ? (
                        <NotionOAuthForm workspaceId={workspace.id} compact />
                      ) : (
                        <UpsertForm
                          workspaceId={workspace.id}
                          kind={i.kind}
                          config={i.config}
                          meta={meta}
                          compact
                        />
                      )}
                      <ProbeForm workspaceId={workspace.id} kind={i.kind} />
                      <DeleteForm workspaceId={workspace.id} kind={i.kind} />
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card className="mb-8">
        <CardHeader
          title="Native provider installs"
          subtitle="First-party installs used by Ship adapters, CLI calls, and knowledge sync."
        />
        {nativeRows.length === 0 ? (
          <p className="text-sm text-white/55">
            No native providers connected yet. Use Atlassian below for Jira +
            Confluence.
          </p>
        ) : (
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {nativeRows.map((i) => (
              <li
                key={i.id}
                className="flex items-start gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3"
              >
                <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-white/15 to-white/[0.02] text-xs font-bold uppercase text-white/85">
                  {i.provider.slice(0, 2)}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-semibold text-white">
                      {nativeName(i.provider)}
                    </span>
                    <Badge
                      tone={
                        i.status === "ready"
                          ? "ok"
                          : i.status === "error"
                          ? "warn"
                          : "neutral"
                      }
                      dot
                    >
                      {i.status}
                    </Badge>
                  </div>
                  <div className="mt-0.5 text-[11px] text-white/55">
                    {i.external_account_name ?? i.external_account_id}
                    {i.disabled_at
                      ? " · disabled"
                      : i.has_credential
                      ? " · credential stored"
                      : " · no credential"}
                  </div>
                  <div className="mt-0.5 text-[10px] text-white/40">
                    {i.capabilities.join(" · ") || "no capabilities"}
                  </div>
                  {!i.disabled_at && (
                    <div className="mt-2 flex flex-wrap items-center gap-3">
                      <NativeProbeForm
                        workspaceId={workspace.id}
                        installationId={i.id}
                      />
                      <NativeDisconnectForm
                        workspaceId={workspace.id}
                        installationId={i.id}
                      />
                    </div>
                  )}
                  {!i.disabled_at &&
                    (i.provider === "azure_devops" || i.provider === "gitlab") && (
                      <NativeRepoBindingForm
                        workspaceId={workspace.id}
                        installationId={i.id}
                        resources={nativeRepoResources[i.id] ?? []}
                        bindings={nativeBindings[i.id] ?? []}
                      />
                    )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Available"
          subtitle="One-click setup; secrets are encrypted with the workspace key and never leave Postgres."
        />
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {nativeAvailable.map((c) => (
            <div
              key={c.id}
              className="rounded-xl border border-aqua/20 bg-aqua/[0.03] p-4 transition hover:border-aqua/40"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-white">{c.name}</span>
                <Badge tone="neutral">{c.group}</Badge>
              </div>
              <p className="mt-1.5 line-clamp-3 text-[11px] text-white/55">
                {c.body}
              </p>
              {c.id === "linear" ? (
                <LinearOAuthForm workspaceId={workspace.id} />
              ) : c.id === "atlassian" ? (
                <AtlassianNativeForm workspaceId={workspace.id} />
              ) : c.id === "azure_devops" ? (
                <AzureDevOpsNativeForm workspaceId={workspace.id} />
              ) : c.id === "gitlab" ? (
                <GitLabNativeForm workspaceId={workspace.id} />
              ) : null}
            </div>
          ))}
          {available.map((c) => (
            <div
              key={c.id}
              className="rounded-xl border border-white/10 bg-white/[0.02] p-4 transition hover:border-white/20"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-white">{c.name}</span>
                <Badge tone="neutral">{c.group}</Badge>
              </div>
              <p className="mt-1.5 line-clamp-3 text-[11px] text-white/55">{c.body}</p>
              {c.id === "notion" ? (
                <NotionOAuthForm workspaceId={workspace.id} />
              ) : (
                <UpsertForm workspaceId={workspace.id} kind={c.id} meta={c} />
              )}
            </div>
          ))}
        </div>
      </Card>
    </AppShell>
  );
}

function nativeName(provider: string): string {
  if (provider === "linear") return "Linear";
  if (provider === "atlassian") return "Jira + Confluence";
  if (provider === "azure_devops") return "Azure DevOps";
  if (provider === "gitlab") return "GitLab";
  return provider;
}

function isShadowedByNativeProvider(kind: string, nativeProviders: Set<string>): boolean {
  if (kind === "notion" && nativeProviders.has("notion")) return true;
  if (kind === "linear" && nativeProviders.has("linear")) return true;
  if (kind === "confluence" && nativeProviders.has("atlassian")) return true;
  return false;
}

function LinearOAuthForm({ workspaceId }: { workspaceId: string }) {
  return (
    <form action="/api/integrations/linear" method="POST" className="mt-3">
      <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
      <button
        type="submit"
        className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-bold text-aqua hover:bg-aqua/20"
      >
        Connect with Linear →
      </button>
      <p className="mt-2 text-[10px] text-white/45">
        Opens Linear OAuth. Ship stores the resulting workspace token encrypted.
      </p>
    </form>
  );
}

function AtlassianNativeForm({ workspaceId }: { workspaceId: string }) {
  return (
    <details className="mt-3">
      <summary className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-bold text-aqua hover:bg-aqua/20">
        Connect →
      </summary>
      <form
        action="/api/integrations/native-atlassian"
        method="POST"
        className="mt-3 space-y-3 rounded-lg border border-white/10 bg-ink/40 p-3"
      >
        <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            Atlassian site
          </span>
          <input
            name="site"
            placeholder="yourorg.atlassian.net"
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs text-white outline-none focus:border-aqua/40"
            suppressHydrationWarning
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            Account email
          </span>
          <input
            name="email"
            type="email"
            placeholder="you@company.com"
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs text-white outline-none focus:border-aqua/40"
            suppressHydrationWarning
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            API token
          </span>
          <input
            name="api_token"
            type="password"
            autoComplete="off"
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 font-mono text-xs text-white outline-none focus:border-aqua/40"
            suppressHydrationWarning
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            Default Jira project
          </span>
          <input
            name="jira_project"
            placeholder="ENG"
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs text-white outline-none focus:border-aqua/40"
            suppressHydrationWarning
          />
        </label>
        <div className="flex items-center justify-end">
          <button
            type="submit"
            className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3 py-1.5 text-[11px] font-bold text-ink hover:brightness-110"
          >
            Save Atlassian
          </button>
        </div>
      </form>
    </details>
  );
}

function AzureDevOpsNativeForm({ workspaceId }: { workspaceId: string }) {
  return (
    <details className="mt-3">
      <summary className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-bold text-aqua hover:bg-aqua/20">
        Connect →
      </summary>
      <form
        action="/api/integrations/native-azure-devops"
        method="POST"
        className="mt-3 space-y-3 rounded-lg border border-white/10 bg-ink/40 p-3"
      >
        <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            Organization
          </span>
          <input
            name="organization"
            placeholder="acme-corp"
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs text-white outline-none focus:border-aqua/40"
            suppressHydrationWarning
          />
          <span className="mt-1 block text-[10px] text-white/40">
            Use the org slug from dev.azure.com/acme-corp, not the full URL.
          </span>
        </label>
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            Default project
          </span>
          <input
            name="project"
            placeholder="Platform"
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs text-white outline-none focus:border-aqua/40"
            suppressHydrationWarning
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            Personal access token
          </span>
          <input
            name="pat"
            type="password"
            autoComplete="off"
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 font-mono text-xs text-white outline-none focus:border-aqua/40"
            suppressHydrationWarning
          />
          <span className="mt-1 block text-[10px] text-white/40">
            Recommended scopes: Code read and Build execute/read for the first
            orchestrator pass.
          </span>
        </label>
        <div className="flex items-center justify-end">
          <button
            type="submit"
            className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3 py-1.5 text-[11px] font-bold text-ink hover:brightness-110"
          >
            Save Azure DevOps
          </button>
        </div>
      </form>
    </details>
  );
}

function GitLabNativeForm({ workspaceId }: { workspaceId: string }) {
  return (
    <details className="mt-3">
      <summary className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-bold text-aqua hover:bg-aqua/20">
        Connect →
      </summary>
      <form
        action="/api/integrations/native-gitlab"
        method="POST"
        className="mt-3 space-y-3 rounded-lg border border-white/10 bg-ink/40 p-3"
      >
        <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            GitLab host
          </span>
          <input
            name="host"
            placeholder="gitlab.com"
            defaultValue="gitlab.com"
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs text-white outline-none focus:border-aqua/40"
            suppressHydrationWarning
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            Default group
          </span>
          <input
            name="group"
            placeholder="platform/core"
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs text-white outline-none focus:border-aqua/40"
            suppressHydrationWarning
          />
          <span className="mt-1 block text-[10px] text-white/40">
            Optional. Use a full group path for scoped repo discovery.
          </span>
        </label>
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            Personal access token
          </span>
          <input
            name="pat"
            type="password"
            autoComplete="off"
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 font-mono text-xs text-white outline-none focus:border-aqua/40"
            suppressHydrationWarning
          />
          <span className="mt-1 block text-[10px] text-white/40">
            Recommended scopes: read_api and read_repository for the first
            GitLab code-host pass.
          </span>
        </label>
        <div className="flex items-center justify-end">
          <button
            type="submit"
            className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3 py-1.5 text-[11px] font-bold text-ink hover:brightness-110"
          >
            Save GitLab
          </button>
        </div>
      </form>
    </details>
  );
}

function NativeDisconnectForm({
  workspaceId,
  installationId,
}: {
  workspaceId: string;
  installationId: string;
}) {
  return (
    <form action="/api/integrations/native-delete" method="POST" className="contents">
      <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
      <input
        type="hidden"
        name="installation_id"
        value={installationId}
        suppressHydrationWarning
      />
      <button
        type="submit"
        className="text-[10px] font-semibold text-coral/80 hover:text-coral"
      >
        Disconnect
      </button>
    </form>
  );
}

function NativeProbeForm({
  workspaceId,
  installationId,
}: {
  workspaceId: string;
  installationId: string;
}) {
  return (
    <form action="/api/integrations/native-probe" method="POST" className="contents">
      <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
      <input
        type="hidden"
        name="installation_id"
        value={installationId}
        suppressHydrationWarning
      />
      <button
        type="submit"
        className="text-[10px] font-semibold text-aqua/80 hover:text-aqua"
        title="Re-run the native provider health probe"
      >
        Probe now
      </button>
    </form>
  );
}

function NativeRepoBindingForm({
  workspaceId,
  installationId,
  resources,
  bindings,
}: {
  workspaceId: string;
  installationId: string;
  resources: ApiNativeResource[];
  bindings: ApiNativeBinding[];
}) {
  const activeBindingCount = bindings.filter((b) => b.status !== "disabled").length;
  return (
    <details className="mt-3 rounded-lg border border-white/10 bg-white/[0.02] p-2">
      <summary className="cursor-pointer text-[10px] font-semibold uppercase tracking-widest text-white/55">
        Repos · {activeBindingCount} selected
      </summary>
      {resources.length === 0 ? (
        <p className="mt-2 text-[10px] text-white/45">
          No repos discovered yet. Check provider permissions, default group/project,
          or run Probe now.
        </p>
      ) : (
        <form action="/api/integrations/native-bindings" method="POST" className="mt-2">
          <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
          <input
            type="hidden"
            name="installation_id"
            value={installationId}
            suppressHydrationWarning
          />
          <div className="max-h-48 space-y-1 overflow-auto pr-1">
            {resources.map((resource) => (
              <label
                key={resource.external_id}
                className="flex items-start gap-2 rounded border border-white/5 bg-ink/30 px-2 py-1.5 text-[11px] text-white/70"
              >
                <input
                  type="checkbox"
                  name="external_id"
                  value={resource.external_id}
                  defaultChecked={resource.bound}
                  className="mt-0.5 accent-aqua"
                  suppressHydrationWarning
                />
                <span className="min-w-0">
                  <span className="block truncate font-semibold text-white/85">
                    {resource.display_name}
                  </span>
                  <span className="block truncate text-[10px] text-white/40">
                    {String(resource.config.default_branch ?? "main")}
                    {resource.external_url ? ` · ${resource.external_url}` : ""}
                  </span>
                </span>
              </label>
            ))}
          </div>
          <div className="mt-2 flex justify-end">
            <button
              type="submit"
              className="rounded-full bg-aqua/15 px-3 py-1 text-[10px] font-bold text-aqua hover:bg-aqua/25"
            >
              Save repo access
            </button>
          </div>
        </form>
      )}
    </details>
  );
}

function NotionOAuthForm({
  workspaceId,
  compact,
}: {
  workspaceId: string;
  compact?: boolean;
}) {
  return (
    <form action="/api/integrations/notion" method="POST" className={compact ? "" : "mt-3"}>
      <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
      <button
        type="submit"
        className={
          compact
            ? "cursor-pointer text-[10px] font-semibold text-aqua/85 hover:text-aqua"
            : "inline-flex cursor-pointer items-center gap-1 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-bold text-aqua hover:bg-aqua/20"
        }
      >
        {compact ? "Reconnect OAuth" : "Connect with Notion →"}
      </button>
      {!compact && (
        <p className="mt-2 text-[10px] text-white/45">
          Opens Notion OAuth. Share the pages/databases Ship should index after
          approval.
        </p>
      )}
    </form>
  );
}

function summariseConfig(config: Record<string, unknown>): string {
  const entries = Object.entries(config).filter(([, v]) => v !== "" && v != null);
  if (entries.length === 0) return "no extra config";
  return entries
    .slice(0, 3)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ");
}

function UpsertForm({
  workspaceId,
  kind,
  meta,
  config,
  compact,
}: {
  workspaceId: string;
  kind: string;
  meta?: (typeof CATALOG)[number];
  config?: Record<string, unknown>;
  compact?: boolean;
}) {
  const fields = meta?.fields ?? [];
  return (
    <details className={compact ? "w-full" : "mt-3"}>
      <summary
        className={
          compact
            ? "cursor-pointer text-[10px] font-semibold text-aqua/85 hover:text-aqua"
            : "inline-flex cursor-pointer items-center gap-1 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-bold text-aqua hover:bg-aqua/20"
        }
      >
        {compact ? "Rotate / edit" : "Connect →"}
      </summary>
      <form
        action="/api/integrations/upsert"
        method="POST"
        className="mt-3 space-y-3 rounded-lg border border-white/10 bg-ink/40 p-3"
      >
        {/*
          Browser-side autofill (Chrome's built-in PasswordManager and
          extensions like 1Password / Bitwarden) decorate `<input>` elements
          with style/data attributes the moment the form lands in the DOM.
          When that mutation lands in the same tick as React's hydration we
          get a spurious `#418 HTML mismatch`. `suppressHydrationWarning`
          tells React to trust the live attributes for that element only.
        */}
        <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
        <input type="hidden" name="kind" value={kind} suppressHydrationWarning />
        {fields.map((f) => (
          <label key={f.name} className="block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
              {f.label}
            </span>
            <input
              name={`config_${f.name}`}
              type="text"
              defaultValue={config?.[f.name] ? String(config[f.name]) : ""}
              placeholder={f.placeholder}
              className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs text-white outline-none focus:border-aqua/40"
              suppressHydrationWarning
            />
          </label>
        ))}
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            {meta?.secretLabel ?? "Secret"}
          </span>
          <input
            name="secret"
            type="password"
            autoComplete="off"
            placeholder={config ? "Leave blank to keep existing" : "Required"}
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 font-mono text-xs text-white outline-none focus:border-aqua/40"
            suppressHydrationWarning
          />
        </label>
        <div className="flex items-center justify-end">
          <button
            type="submit"
            className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3 py-1.5 text-[11px] font-bold text-ink hover:brightness-110"
          >
            Save secret
          </button>
        </div>
      </form>
    </details>
  );
}

function DeleteForm({ workspaceId, kind }: { workspaceId: string; kind: string }) {
  return (
    <form action="/api/integrations/delete" method="POST" className="contents">
      <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
      <input type="hidden" name="kind" value={kind} suppressHydrationWarning />
      <button
        type="submit"
        className="text-[10px] font-semibold text-coral/80 hover:text-coral"
      >
        Disconnect
      </button>
    </form>
  );
}

function ProbeForm({ workspaceId, kind }: { workspaceId: string; kind: string }) {
  return (
    <form action="/api/integrations/probe" method="POST" className="contents">
      <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
      <input type="hidden" name="kind" value={kind} suppressHydrationWarning />
      <button
        type="submit"
        className="text-[10px] font-semibold text-aqua/80 hover:text-aqua"
        title="Re-run the health probe right now"
      >
        Probe now
      </button>
    </form>
  );
}

function HealthLine({
  lastAt,
  lastError,
  status,
}: {
  lastAt: string | null;
  lastError: string | null;
  status: string;
}) {
  // Render an absolute UTC timestamp so server and client always agree on
  // the wire string — relative ("3m ago") strings would re-format on the
  // client and trip React's hydration check.
  if (!lastAt) {
    return (
      <div className="mt-0.5 text-[10px] text-white/40">
        Awaiting first probe (worker runs every 30s).
      </div>
    );
  }
  const at = new Date(lastAt).toUTCString();
  if (status === "error" && lastError) {
    return (
      <div className="mt-0.5 text-[10px] text-coral/85">
        Probed {at} · <span className="font-mono">{lastError}</span>
      </div>
    );
  }
  return <div className="mt-0.5 text-[10px] text-white/40">Probed {at}</div>;
}

function MockView({ reason }: { reason: string }) {
  const ws = mockWorkspaces[0];
  return (
    <AppShell
      kicker={`${ws.name} · integrations`}
      title="Integrations"
      actions={<ButtonPrimary>+ Custom integration</ButtonPrimary>}
    >
      <MockBanner reason={reason} />
      <Card className="mb-8">
        <CardHeader title="Connected" subtitle="Configured for this workspace" />
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {mockIntegrations.map((i) => (
            <li
              key={i.id}
              className="flex items-start gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3"
            >
              <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-white/15 to-white/[0.02] text-xs font-bold uppercase text-white/85">
                {i.kind.slice(0, 2)}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-semibold text-white">{i.label}</span>
                  <Badge
                    tone={i.status === "connected" ? "ok" : i.status === "warning" ? "warn" : "neutral"}
                    dot
                  >
                    {i.status}
                  </Badge>
                </div>
                <div className="mt-0.5 text-[11px] text-white/55">{i.detail}</div>
                <div className="mt-2 flex items-center gap-2">
                  <ButtonGhost className="!py-1 !text-[10px]">Edit</ButtonGhost>
                  <button className="text-[10px] font-semibold text-coral/80 hover:text-coral">
                    Disconnect
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </AppShell>
  );
}
