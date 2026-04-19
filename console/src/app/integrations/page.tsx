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
  listWorkspaces,
} from "@/lib/api/client";
import type { ApiIntegration, ApiWorkspace } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";
import { integrations as mockIntegrations, workspaces as mockWorkspaces } from "@/lib/mock/cloud";

export const dynamic = "force-dynamic";

type Mode =
  | { source: "live"; workspace: ApiWorkspace; rows: ApiIntegration[] }
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
    id: "linear",
    name: "Linear",
    group: "Tracker",
    body: "Mirror approved retro/daily action items as issues.",
    fields: [{ name: "team_id", label: "Team ID", placeholder: "ENG" }],
    secretLabel: "Linear API key (lin_api_…)",
  },
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
    id: "gitlab",
    name: "GitLab",
    group: "Source",
    body: "MR review and CI status; self-hosted instance supported.",
    fields: [
      { name: "host", label: "Host", placeholder: "gitlab.com" },
      { name: "group", label: "Group", placeholder: "your-group" },
    ],
    secretLabel: "Project access token",
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

async function load(): Promise<Mode> {
  if (!isApiConfigured()) return { source: "mock", reason: "SHIP_API_URL not set" };
  const token = await getSessionToken();
  if (!token) return { source: "mock", reason: "Sign in to manage real integrations" };
  try {
    const ws = await listWorkspaces(token);
    if (ws.length === 0)
      return { source: "mock", reason: "Create a workspace first to wire integrations" };
    const target = ws[0];
    const rows = await listIntegrations(target.id, token);
    return { source: "live", workspace: target, rows };
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

export default async function IntegrationsPage() {
  const data = await load();

  if (data.source === "mock") {
    return <MockView reason={data.reason} />;
  }

  const { workspace, rows } = data;
  const connectedIds = new Set(rows.map((r) => r.kind));
  const available = CATALOG.filter((c) => !connectedIds.has(c.id));

  return (
    <AppShell
      kicker={`${workspace.name} · integrations`}
      title="Integrations"
      actions={<ButtonPrimary>+ Custom integration</ButtonPrimary>}
    >
      <LiveBanner workspace={workspace.slug} />

      <Card className="mb-8">
        <CardHeader
          title="Connected"
          subtitle="Configured for this workspace · secrets are encrypted at rest"
        />
        {rows.length === 0 ? (
          <p className="text-sm text-white/55">
            Nothing connected yet. Pick one below to drop in your first secret.
          </p>
        ) : (
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {rows.map((i) => {
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
                      <UpsertForm
                        workspaceId={workspace.id}
                        kind={i.kind}
                        config={i.config}
                        meta={meta}
                        compact
                      />
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

      <Card>
        <CardHeader
          title="Available"
          subtitle="One-click setup; secrets are encrypted with the workspace key and never leave Postgres."
        />
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
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
              <UpsertForm workspaceId={workspace.id} kind={c.id} meta={c} />
            </div>
          ))}
        </div>
      </Card>
    </AppShell>
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
