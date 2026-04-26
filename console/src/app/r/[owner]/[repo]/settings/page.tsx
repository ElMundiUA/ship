import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import {
  Badge,
  type BadgeTone,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
} from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  checkAgentSecrets,
  getRepoConfig,
  getRepoHome,
  getRepoTrackerBinding,
  isApiConfigured,
  listRepoSecrets,
  listRequiredSecrets,
  type ApiActivatedRepo,
  type ApiAgentSecretCheck,
  type ApiRepoConfig,
  type ApiRepoHomeReport,
  type ApiRepoSecret,
  type ApiRequiredSecret,
  type ApiTrackerBinding,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { resolveRepoContext, type RepoContext } from "@/lib/repo-context";
import { slugFromParams, type RepoRouteParams } from "@/lib/repo-slug";
import { toAppShellWorkspaces } from "@/lib/workspace-scope";

/**
 * Repo-mode Settings (``/r/<owner>/<repo>/settings``).
 *
 * Not a copy of the workspace ``/settings`` page — that surface owns
 * tokens, catalog-source toggles, tenant-wide things. This one is a
 * composition over repo-scoped endpoints (RFC-0008 PR-6 Deferred, per
 * internal console-refactor-backlog):
 *
 * - Repo facts + GitHub link (from ``listActivatedRepos`` via ctx)
 * - Bundle & install PR (``getRepoHome`` flags + ``installBundle`` form)
 * - ``.ship/config.yml`` preview (``getRepoConfig``)
 * - Tracker binding summary (``getRepoTrackerBinding``)
 * - Ship-managed Actions secrets summary (``listRepoSecrets`` +
 *   ``listRequiredSecrets``) — rich edit UI stays in
 *   ``/repos/{repo_id}/secrets`` to avoid duplicating the form surface.
 * - Agent secrets health (``checkAgentSecrets``)
 * - Danger zone: disconnect repo (reuses existing
 *   ``/api/dashboard/disconnect-repo`` handler).
 *
 * Every section fetches independently and degrades to an inline error
 * card on failure so a transient 502 on (say) tracker doesn't blank
 * out the page for the user trying to rotate a secret.
 */

export const dynamic = "force-dynamic";

type SearchParamsBag = Record<string, string | string[] | undefined>;

type SettingsBundle = {
  home: ApiRepoHomeReport | null;
  homeError: string | null;
  config: ApiRepoConfig | null;
  configError: string | null;
  tracker: ApiTrackerBinding | null;
  trackerError: string | null;
  secrets: ApiRepoSecret[] | null;
  required: ApiRequiredSecret[] | null;
  secretsError: string | null;
  agents: ApiAgentSecretCheck | null;
  agentsError: string | null;
};

export default async function RepoSettingsPage({
  params,
  searchParams,
}: {
  params: Promise<RepoRouteParams>;
  searchParams?: Promise<SearchParamsBag>;
}) {
  const [resolved, rawSearch] = await Promise.all([
    params,
    searchParams ?? Promise.resolve({} as SearchParamsBag),
  ]);
  const slug = slugFromParams(resolved);
  if (!slug) notFound();
  const basePath = `/r/${slug}/settings`;

  if (!isApiConfigured()) {
    return (
      <AppShell title="Settings" kicker={`${slug} · repo`}>
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to manage this repo."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect(`/login?next=${encodeURIComponent(basePath)}`);

  const result = await resolveRepoContext(token, slug, rawSearch);
  if (result.kind === "unauthorized") {
    redirect(`/login?next=${encodeURIComponent(basePath)}`);
  }
  if (result.kind === "down") return renderUnavailable(slug);
  if (result.kind === "empty") redirect("/onboarding?step=github");
  if (result.kind === "not-found") notFound();

  const ctx = result.ctx;
  const bundle = await loadBundle(ctx);

  return renderShell(ctx, bundle);
}

async function loadBundle(ctx: RepoContext): Promise<SettingsBundle> {
  const { workspace, repo, token } = ctx;
  const [homeRes, configRes, trackerRes, secretsRes, requiredRes, agentsRes] =
    await Promise.allSettled([
      getRepoHome(workspace.id, repo.id, { token }),
      getRepoConfig(workspace.id, repo.id, token),
      getRepoTrackerBinding(workspace.id, repo.id, token),
      listRepoSecrets(workspace.id, repo.id, token),
      listRequiredSecrets(workspace.id, repo.id, token),
      checkAgentSecrets(workspace.id, repo.id, { token }),
    ]);

  return {
    home: homeRes.status === "fulfilled" ? homeRes.value : null,
    homeError: homeRes.status === "rejected" ? describe(homeRes.reason) : null,
    config: configRes.status === "fulfilled" ? configRes.value : null,
    configError:
      configRes.status === "rejected" ? describe(configRes.reason) : null,
    tracker: trackerRes.status === "fulfilled" ? trackerRes.value : null,
    trackerError:
      trackerRes.status === "rejected" ? describe(trackerRes.reason) : null,
    secrets:
      secretsRes.status === "fulfilled" ? secretsRes.value.items : null,
    required:
      requiredRes.status === "fulfilled" ? requiredRes.value.items : null,
    secretsError:
      secretsRes.status === "rejected"
        ? describe(secretsRes.reason)
        : requiredRes.status === "rejected"
          ? describe(requiredRes.reason)
          : null,
    agents: agentsRes.status === "fulfilled" ? agentsRes.value : null,
    agentsError:
      agentsRes.status === "rejected" ? describe(agentsRes.reason) : null,
  };
}

function describe(err: unknown): string {
  if (err instanceof ApiHttpError) return `HTTP ${err.status}`;
  if (err instanceof ApiUnavailableError) return "Backend unreachable";
  return "Unknown error";
}

function renderShell(ctx: RepoContext, bundle: SettingsBundle) {
  const { workspace, allWorkspaces, repo, repos } = ctx;
  const base = `/r/${repo.full_name}`;
  const multi = allWorkspaces.length > 1;
  const homeHref = multi
    ? `${base}?ws=${encodeURIComponent(workspace.id)}`
    : base;
  return (
    <AppShell
      title="Settings"
      kicker={`${repo.full_name} · repo`}
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      allWorkspaces={toAppShellWorkspaces(allWorkspaces)}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: repo.id,
      }}
      actions={
        <div className="flex flex-wrap items-center gap-3">
          <Link
            href={`/onboarding?step=repos&ws=${encodeURIComponent(workspace.id)}`}
            className="rounded-full border border-aqua/35 bg-aqua/10 px-3 py-1 text-xs font-bold text-aqua/90 transition hover:bg-aqua/20"
          >
            + Add repository
          </Link>
          <Link
            href={homeHref}
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            ← Repo home
          </Link>
        </div>
      }
    >
      <div className="space-y-5">
        <RepoFactsCard repo={repo} />
        <BundleCard
          workspace={workspace.id}
          repo={repo}
          home={bundle.home}
          homeError={bundle.homeError}
        />
        <ConfigCard
          config={bundle.config}
          error={bundle.configError}
          repoFullName={repo.full_name}
          editHref={`${base}/lanes?tab=library`}
        />
        <TrackerCard
          tracker={bundle.tracker}
          error={bundle.trackerError}
        />
        <SecretsCard
          secrets={bundle.secrets}
          required={bundle.required}
          error={bundle.secretsError}
          secretsHref={`/repos/${repo.id}/secrets`}
        />
        <AgentSecretsCard
          agents={bundle.agents}
          error={bundle.agentsError}
        />
        <DangerZone
          workspaceId={workspace.id}
          repo={repo}
        />
      </div>
    </AppShell>
  );
}

function renderUnavailable(slug: string) {
  return (
    <AppShell title="Settings" kicker={`${slug} · repo`}>
      <Card>
        <CardHeader
          title="Backend unreachable"
          subtitle="Settings couldn't load. Try again in a few seconds."
        />
      </Card>
    </AppShell>
  );
}

// ---------------------------------------------------------------------------
// Repo facts
// ---------------------------------------------------------------------------

function RepoFactsCard({ repo }: { repo: ApiActivatedRepo }) {
  return (
    <Card>
      <CardHeader
        title="Repo facts"
        subtitle={repo.private ? "Private · GitHub" : "Public · GitHub"}
      />
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Full name" value={repo.full_name} mono />
        <Field label="Default branch" value={repo.default_branch} mono />
        <Field
          label="Preset"
          value={repo.preset ?? "adoption-minimum (implicit)"}
        />
        <Field
          label="Activated"
          value={repo.activated_at ? relativeDate(repo.activated_at) : "—"}
        />
      </dl>
      <div className="mt-4 flex flex-wrap gap-3 text-xs">
        <a
          href={repo.html_url}
          target="_blank"
          rel="noreferrer"
          className="font-semibold text-aqua hover:underline"
        >
          View on GitHub ↗
        </a>
        {repo.description ? (
          <span className="text-white/55">{repo.description}</span>
        ) : null}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Bundle
// ---------------------------------------------------------------------------

function BundleCard({
  workspace,
  repo,
  home,
  homeError,
}: {
  workspace: string;
  repo: ApiActivatedRepo;
  home: ApiRepoHomeReport | null;
  homeError: string | null;
}) {
  const installed = repo.installed_bundle_version;
  const current = repo.current_bundle_version;
  const drift = home?.now.bundle_drift ?? (installed !== current && installed !== null);
  const missingInstall = home?.now.install_missing ?? false;
  const suspended = home?.now.install_suspended ?? false;

  const tone: BadgeTone = missingInstall || suspended
    ? "err"
    : drift
      ? "warn"
      : installed === null
        ? "warn"
        : "ok";
  const label = missingInstall
    ? "app missing"
    : suspended
      ? "app suspended"
      : installed === null
        ? "never seeded"
        : drift
          ? "bundle out of date"
          : "up to date";

  return (
    <Card>
      <CardHeader
        title="Bundle & install PR"
        subtitle="Current wizard seed: workflows, .ship/config.yml, FSM, and post-merge bootstrap."
      />
      <div className="mb-4 flex flex-wrap items-center gap-3 text-xs">
        <Badge tone={tone} dot>
          {label}
        </Badge>
        <span className="font-mono text-white/65">
          installed: {installed === null ? "—" : `v${installed}`} · current: v{current}
        </span>
        {homeError ? (
          <span className="text-white/45">home rollup: {homeError}</span>
        ) : null}
      </div>
      <form
        action="/api/dashboard/install-bundle"
        method="POST"
        className="flex flex-wrap items-end gap-3"
      >
        <input type="hidden" name="ws" value={workspace} />
        <input type="hidden" name="repo_id" value={repo.id} />
        <button
          type="submit"
          className="inline-flex items-center rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-1.5 text-[11px] font-bold text-ink shadow-glow transition hover:brightness-110"
        >
          Open wizard seed PR
        </button>
        <p className="text-[11px] text-white/45">
          Opens the infra PR; generated knowledge follows in a second PR after merge.
        </p>
      </form>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// .ship/config.yml
// ---------------------------------------------------------------------------

function ConfigCard({
  config,
  error,
  repoFullName,
  editHref,
}: {
  config: ApiRepoConfig | null;
  error: string | null;
  repoFullName: string;
  editHref: string;
}) {
  if (error && !config) {
    return (
      <Card>
        <CardHeader
          title=".ship/config.yml"
          subtitle="Couldn't load the repo config."
        />
        <p className="text-xs text-white/55">{error}</p>
      </Card>
    );
  }
  if (!config) return null;

  const lanes = config.parsed?.lanes
    ? Object.keys(config.parsed.lanes).length
    : 0;

  return (
    <Card>
      <CardHeader
        title=".ship/config.yml"
        subtitle={
          config.exists
            ? `Lives on ${repoFullName}@${config.default_branch}.`
            : `No config on ${repoFullName}@${config.default_branch} yet.`
        }
      />
      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs">
        {config.exists ? (
          <Badge tone="ok" dot>
            present
          </Badge>
        ) : (
          <Badge tone="warn" dot>
            missing
          </Badge>
        )}
        {config.parsed ? (
          <span className="font-mono text-white/65">
            version: {config.parsed.version ?? "—"} · preset:{" "}
            {config.parsed.preset ?? "—"} · lanes: {lanes}
          </span>
        ) : config.parse_error ? (
          <Badge tone="err">parse error</Badge>
        ) : null}
      </div>
      {config.parse_error ? (
        <pre className="mb-3 max-h-32 overflow-auto rounded-md border border-coral/30 bg-coral/10 p-3 text-[11px] text-coral/90">
          {config.parse_error}
        </pre>
      ) : null}
      {config.raw_yaml ? (
        <pre className="max-h-72 overflow-auto rounded-md border border-white/10 bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-white/75">
          {config.raw_yaml}
        </pre>
      ) : (
        <p className="text-xs text-white/55">
          Use the Library tab to seed a config or enable lanes.
        </p>
      )}
      <div className="mt-3">
        <ButtonGhost>
          <Link href={editHref}>Edit in Library →</Link>
        </ButtonGhost>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Tracker binding
// ---------------------------------------------------------------------------

function TrackerCard({
  tracker,
  error,
}: {
  tracker: ApiTrackerBinding | null;
  error: string | null;
}) {
  if (error && !tracker) {
    return (
      <Card>
        <CardHeader
          title="Tracker"
          subtitle="Couldn't load the tracker binding."
        />
        <p className="text-xs text-white/55">{error}</p>
      </Card>
    );
  }
  if (!tracker) return null;

  const kind = tracker.kind ?? tracker.workspace_default_kind;
  const sourceLabel =
    tracker.source === "repo"
      ? "per-repo binding"
      : tracker.source === "workspace"
        ? "workspace default (inherited)"
        : "not bound";
  const tone: BadgeTone =
    tracker.source === "none" ? "warn" : tracker.kind ? "ok" : "info";

  return (
    <Card>
      <CardHeader
        title="Tracker"
        subtitle="Where clarifications + improvements are projected as tickets."
      />
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <Badge tone={tone} dot>
          {kind ?? "none"}
        </Badge>
        <span className="text-white/65">{sourceLabel}</span>
      </div>
      <div className="mt-4">
        <ButtonGhost>
          <Link href={`/onboarding?step=tracker`}>Change tracker →</Link>
        </ButtonGhost>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Ship-managed Actions secrets
// ---------------------------------------------------------------------------

function SecretsCard({
  secrets,
  required,
  error,
  secretsHref,
}: {
  secrets: ApiRepoSecret[] | null;
  required: ApiRequiredSecret[] | null;
  error: string | null;
  secretsHref: string;
}) {
  if (error && !secrets) {
    return (
      <Card>
        <CardHeader
          title="Ship-managed Actions secrets"
          subtitle="Couldn't load the secrets matrix."
        />
        <p className="text-xs text-white/55">{error}</p>
      </Card>
    );
  }
  const stored = secrets ?? [];
  const req = required ?? [];
  const missing = req.filter((r) => !r.stored);
  return (
    <Card>
      <CardHeader
        title="Ship-managed Actions secrets"
        subtitle="Encrypted at rest and mirrored to GitHub Actions."
      />
      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs">
        <Badge tone={missing.length === 0 ? "ok" : "err"} dot>
          {missing.length === 0
            ? "required secrets present"
            : `${missing.length} missing required`}
        </Badge>
        <span className="font-mono text-white/65">
          stored: {stored.length} · required: {req.length}
        </span>
      </div>
      {missing.length > 0 ? (
        <ul className="mb-3 space-y-1 text-xs text-white/75">
          {missing.slice(0, 6).map((m) => (
            <li key={m.name} className="flex items-center gap-2">
              <Badge tone="err">missing</Badge>
              <span className="font-mono">{m.name}</span>
              <span className="text-white/45">
                for {m.required_by.join(", ")}
              </span>
            </li>
          ))}
          {missing.length > 6 ? (
            <li className="text-white/45">…and {missing.length - 6} more</li>
          ) : null}
        </ul>
      ) : null}
      <ButtonPrimary>
        <Link href={secretsHref}>Manage secrets →</Link>
      </ButtonPrimary>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Agent secrets health
// ---------------------------------------------------------------------------

function AgentSecretsCard({
  agents,
  error,
}: {
  agents: ApiAgentSecretCheck | null;
  error: string | null;
}) {
  if (error && !agents) {
    return (
      <Card>
        <CardHeader
          title="Agent secrets"
          subtitle="Couldn't load the agent secrets health check."
        />
        <p className="text-xs text-white/55">{error}</p>
      </Card>
    );
  }
  if (!agents) return null;
  const rows = agents.agents;
  const missing = rows.filter((a) => a.required && !a.present).length;
  return (
    <Card>
      <CardHeader
        title="Agent secrets"
        subtitle="Vendor API keys the agents need at dispatch time."
      />
      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs">
        <Badge tone={missing === 0 ? "ok" : "err"} dot>
          {missing === 0
            ? "all required present"
            : `${missing} required missing`}
        </Badge>
      </div>
      {rows.length === 0 ? (
        <p className="text-xs text-white/55">
          No agent integrations registered.
        </p>
      ) : (
        <ul className="space-y-1 text-xs">
          {rows.map((row) => (
            <li
              key={row.slug}
              className="flex flex-wrap items-center gap-2 border-b border-white/5 pb-1.5 last:border-b-0"
            >
              <Badge
                tone={row.present ? "ok" : row.required ? "err" : "neutral"}
              >
                {row.present ? "present" : row.required ? "missing" : "n/a"}
              </Badge>
              <span className="font-semibold text-white/85">{row.label}</span>
              {row.secret_name ? (
                <span className="font-mono text-white/55">
                  ({row.secret_name})
                </span>
              ) : null}
              {row.vendor_url ? (
                <a
                  href={row.vendor_url}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-auto text-aqua hover:underline"
                >
                  vendor ↗
                </a>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3">
        <ButtonGhost>
          <Link href={`/onboarding?step=agent-secrets`}>
            Push agent secrets →
          </Link>
        </ButtonGhost>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Danger zone
// ---------------------------------------------------------------------------

function DangerZone({
  workspaceId,
  repo,
}: {
  workspaceId: string;
  repo: ApiActivatedRepo;
}) {
  return (
    <Card className="border-coral/30 bg-coral/5">
      <CardHeader
        title="Danger zone"
        subtitle="Disconnecting removes every pipeline + run bound to this repo. GitHub workflow files stay in the customer repo."
      />
      <form
        action="/api/dashboard/disconnect-repo"
        method="POST"
        className="flex flex-wrap items-center gap-3"
      >
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="repo_id" value={repo.id} />
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-white/50">
            Type &quot;disconnect&quot; to confirm
          </span>
          <input
            type="text"
            name="confirm"
            required
            autoComplete="off"
            pattern="disconnect"
            placeholder="disconnect"
            className="w-56 rounded-md border border-coral/40 bg-black/30 px-3 py-1.5 font-mono text-xs text-white placeholder:text-white/30 focus:border-coral/80 focus:outline-none"
          />
        </label>
        <button
          type="submit"
          className="inline-flex items-center rounded-full border border-coral/60 bg-coral/20 px-4 py-1.5 text-[11px] font-bold text-coral hover:bg-coral/30"
        >
          Disconnect {repo.full_name}
        </button>
      </form>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

function Field({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-white/5 pb-1.5 last:border-b-0">
      <dt className="text-[11px] font-semibold uppercase tracking-widest text-white/45">
        {label}
      </dt>
      <dd
        className={
          "truncate text-right text-[11px] text-white/80 " +
          (mono ? "font-mono" : "")
        }
      >
        {value}
      </dd>
    </div>
  );
}

function relativeDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(0, 10);
}
