/**
 * Workspace settings page.
 *
 * Reads from `/v1/workspaces/{id}` (general + catalog_sources) and
 * `/v1/workspaces/{id}/artifact-repos` (registered repos) when a live
 * session + workspace exist; otherwise falls back to the mock fixtures so
 * the marketing-style preview keeps something to show. Catalog source
 * toggles are wired via tiny native form POSTs to
 * `/api/settings/catalog-sources` so the page stays a server component.
 */

import { cookies } from "next/headers";

import { AppShell } from "@/components/app-shell";
import {
  Badge,
  Card,
  CardHeader,
  LiveBanner,
  MockBanner,
} from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listArtifactRepos,
  listTokens,
  listWorkspaces,
} from "@/lib/api/client";
import type {
  ApiArtifactRepo,
  ApiTokenInfo,
  ApiWorkspace,
} from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";
import { workspaces as mockWorkspaces } from "@/lib/mock/cloud";

export const dynamic = "force-dynamic";

type Mode =
  | {
      source: "live";
      workspace: ApiWorkspace;
      repos: ApiArtifactRepo[];
      tokens: ApiTokenInfo[];
    }
  | { source: "mock"; reason: string };

const CATALOG_KEYS = ["global", "workspace", "project"] as const;
type CatalogKey = (typeof CATALOG_KEYS)[number];

const SOURCE_DESCRIPTIONS: Record<
  CatalogKey,
  { title: string; description: string }
> = {
  global: {
    title: "Global catalog",
    description:
      "Read-only mirror of the public Ship monorepo. Disable for air-gapped enterprise installs.",
  },
  workspace: {
    title: "Workspace catalog",
    description:
      "Artifacts authored by this workspace. Backed by your registered artifact repos.",
  },
  project: {
    title: "Project catalog",
    description:
      "Per-project pins (.ship/artifacts/) — overrides everything else for that project.",
  },
};

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
    case "slug_mismatch":
      return "Slug confirmation didn’t match. Type the workspace slug exactly as shown.";
    case "api_unavailable":
      return "Backend is unreachable right now. Try again in a moment.";
    default:
      return `Couldn't save the change (${code}). Try again or refresh.`;
  }
}

const TABS = [
  { id: "general", label: "General" },
  { id: "catalog", label: "Catalog sources" },
  { id: "repos", label: "Artifact repos" },
  { id: "tokens", label: "Tokens" },
  { id: "danger", label: "Danger zone" },
] as const;
type TabId = (typeof TABS)[number]["id"];
const TAB_IDS = TABS.map((t) => t.id);

function isTabId(value: string): value is TabId {
  return (TAB_IDS as readonly string[]).includes(value);
}

async function load(): Promise<Mode> {
  if (!isApiConfigured()) return { source: "mock", reason: "SHIP_API_URL not set" };
  const token = await getSessionToken();
  if (!token) return { source: "mock", reason: "Sign in to manage real settings" };
  try {
    const ws = await listWorkspaces(token);
    if (ws.length === 0)
      return {
        source: "mock",
        reason: "Create a workspace first to manage settings",
      };
    const target = ws[0];
    // The artifact-repos call may 404 in older deployments; treat it as
    // empty rather than falling all the way back to mock data.
    let repos: ApiArtifactRepo[] = [];
    try {
      repos = await listArtifactRepos(target.id, token);
    } catch (err) {
      if (!(err instanceof ApiHttpError) || err.status !== 404) throw err;
    }
    let tokens: ApiTokenInfo[] = [];
    try {
      tokens = await listTokens(token);
    } catch (err) {
      // Token listing is gated behind the same auth as the rest; on a stale
      // build /v1/auth/tokens may 404 — fall back to an empty list rather
      // than crashing the whole page.
      if (!(err instanceof ApiHttpError) || err.status !== 404) throw err;
    }
    return { source: "live", workspace: target, repos, tokens };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      return { source: "mock", reason: "Session expired — sign in again" };
    if (err instanceof ApiUnavailableError)
      return { source: "mock", reason: "Backend unreachable" };
    return { source: "mock", reason: "Backend returned an error" };
  }
}

export default async function SettingsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const data = await load();
  const params = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as Record<
    string,
    string | string[] | undefined
  >;
  const errorCode = typeof params.error === "string" ? params.error : null;
  const requestedTab = typeof params.tab === "string" ? params.tab : null;
  const activeTab: TabId =
    requestedTab && isTabId(requestedTab) ? requestedTab : "general";
  const justMintedFlag =
    typeof params.just_minted === "string" && params.just_minted === "1";

  if (data.source === "mock") {
    return <MockView reason={data.reason} />;
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

  const { workspace, repos, tokens } = data;
  const sources: Record<CatalogKey, boolean> = {
    global: workspace.catalog_sources?.global ?? true,
    workspace: workspace.catalog_sources?.workspace ?? true,
    project: workspace.catalog_sources?.project ?? true,
  };

  return (
    <AppShell
      kicker={`${workspace.name} · settings`}
      title="Workspace settings"
    >
      <LiveBanner workspace={workspace.slug} />
      {errorCode && (
        <div className="mb-5 rounded-xl border border-coral/30 bg-coral/[0.06] px-3 py-2 text-xs text-coral/95">
          {errorMessage(errorCode)}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <nav className="space-y-1 text-sm">
          {TABS.map((tab) => {
            const current = tab.id === activeTab;
            return (
              <a
                key={tab.id}
                href={`?tab=${tab.id}`}
                className={
                  "block rounded-md px-3 py-1.5 transition " +
                  (current
                    ? "bg-white/[0.06] font-semibold text-white shadow-[inset_2px_0_0_theme(colors.aqua)]"
                    : "text-white/55 hover:bg-white/[0.04] hover:text-white")
                }
              >
                {tab.label}
              </a>
            );
          })}
        </nav>

        <div className="space-y-6">
          {activeTab === "general" && (
            <Card>
              <CardHeader title="General" subtitle="Workspace identity" />
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <Field label="Workspace name" value={workspace.name} readOnly />
                <Field label="Slug (URL handle)" value={workspace.slug} mono readOnly />
                <Field label="Workspace ID" value={workspace.id} mono readOnly />
                <Field
                  label="Created"
                  value={new Date(workspace.created_at).toUTCString()}
                  readOnly
                />
              </div>
            </Card>
          )}

          {activeTab === "catalog" && (
            <Card>
              <CardHeader
                title="Catalog sources"
                subtitle="Which layers are merged when the CLI / UI lists artifacts. Higher layer wins."
              />
              <ul className="space-y-3">
                {CATALOG_KEYS.map((key) => (
                  <SourceToggle
                    key={key}
                    workspaceId={workspace.id}
                    toneKey={key}
                    title={SOURCE_DESCRIPTIONS[key].title}
                    description={SOURCE_DESCRIPTIONS[key].description}
                    on={sources[key]}
                  />
                ))}
              </ul>
              <div className="mt-4 rounded-xl border border-aqua/20 bg-aqua/[0.04] px-3 py-2.5 text-[11px] text-aqua/85">
                <strong>Tip.</strong> When the same id exists in multiple layers,{" "}
                <span className="font-mono">project &gt; workspace &gt; global</span>. The
                CLI shows <span className="font-mono">effective_source</span> on every
                fetched artifact so the agent can audit where the override came from.
              </div>
            </Card>
          )}

          {activeTab === "repos" && (
            <Card>
              <CardHeader
                title="Artifact repos"
                subtitle="Where workspace + project layers are read from. Local file:// works inline; git URLs sync via the worker."
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
          )}

          {activeTab === "tokens" && (
            <TokensPanel
              workspaceId={workspace.id}
              tokens={tokens}
              freshSecret={freshSecret}
            />
          )}

          {activeTab === "danger" && (
            <DangerZone workspace={workspace} />
          )}
        </div>
      </div>
    </AppShell>
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
            title="Token created"
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
          title="Personal access tokens"
          subtitle="Long-lived bearer tokens for the CLI / CI. Each shows once at mint time."
        />
        {tokens.length === 0 ? (
          <p className="mb-4 text-sm text-white/55">
            No PATs yet. Mint one below to drive <code className="font-mono">shipctl</code>{" "}
            from CI or your laptop.
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
            + Mint token
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
            Workspace-scoped tokens can only act on this workspace. Leave TTL
            empty for a non-expiring token (revoke it manually when no longer
            needed).
          </p>
        </details>
      </Card>
    </>
  );
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

function SourceToggle({
  workspaceId,
  toneKey,
  title,
  description,
  on,
}: {
  workspaceId: string;
  toneKey: CatalogKey;
  title: string;
  description: string;
  on: boolean;
}) {
  // The visual toggle is just a label wrapping a hidden submit button. The
  // form posts the *target* state (`!on`) so a single click flips it; no
  // client-side React state needed.
  return (
    <li className="flex items-start justify-between gap-4 rounded-xl border border-white/10 bg-white/[0.02] p-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Badge tone={toneKey}>{toneKey}</Badge>
          <span className="text-sm font-semibold text-white">{title}</span>
        </div>
        <p className="mt-1 text-[11px] leading-snug text-white/55">{description}</p>
      </div>
      <form
        action="/api/settings/catalog-sources"
        method="POST"
        className="shrink-0"
      >
        {/*
          Browser autofill (Chrome PasswordManager + extensions like
          1Password) likes to inject style/data attrs onto every <input>
          right as React hydrates, which trips React #418. Suppressing
          the warning per-input is the React-blessed escape hatch.
        */}
        <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
        <input type="hidden" name="key" value={toneKey} suppressHydrationWarning />
        <input
          type="hidden"
          name="enabled"
          value={on ? "false" : "true"}
          suppressHydrationWarning
        />
        <button
          type="submit"
          aria-label={`Toggle ${title}`}
          className={
            "relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full transition " +
            (on
              ? "bg-aqua/70 hover:bg-aqua/85"
              : "bg-white/10 hover:bg-white/20")
          }
        >
          <span
            className={
              "inline-block h-5 w-5 transform rounded-full bg-white shadow transition " +
              (on ? "translate-x-5" : "translate-x-0.5")
            }
          />
        </button>
      </form>
    </li>
  );
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

function MockView({ reason }: { reason: string }) {
  const ws = mockWorkspaces[0];
  return (
    <AppShell kicker={`${ws.name} · settings`} title="Workspace settings">
      <MockBanner reason={reason} />
      <Card>
        <CardHeader
          title="Catalog sources"
          subtitle="Which layers are merged when the CLI / UI lists artifacts. Higher layer wins."
        />
        <ul className="space-y-3">
          {CATALOG_KEYS.map((key) => (
            <li
              key={key}
              className="flex items-start justify-between gap-4 rounded-xl border border-white/10 bg-white/[0.02] p-3"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <Badge tone={key}>{key}</Badge>
                  <span className="text-sm font-semibold text-white">
                    {SOURCE_DESCRIPTIONS[key].title}
                  </span>
                </div>
                <p className="mt-1 text-[11px] leading-snug text-white/55">
                  {SOURCE_DESCRIPTIONS[key].description}
                </p>
              </div>
              <span className="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full bg-white/10">
                <span className="inline-block h-5 w-5 translate-x-0.5 transform rounded-full bg-white shadow" />
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </AppShell>
  );
}
