"use client";

/**
 * DeploymentsClient — the single home for deploying + monitoring.
 *
 * Model: the primary object is an **app** (repo + provider), not an
 * individual deploy attempt. We group the raw deployment rows by
 * (repo_id, provider); the newest row in a group is the app's current
 * state and the rest are its history. Each app renders as an expandable
 * card: collapsed shows status/URL/health; expanded shows
 * Overview / History / Logs / Settings.
 *
 * Calls go to dedicated static Next route handlers under /api/* (they
 * attach the session bearer server-side). NOTE: never use a dynamic
 * `[param]` route — it's shadowed by the next.config afterFiles rewrite.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ApiActivatedRepo,
  ApiDeployment,
  ApiDeploymentStatus,
  ApiDeployProvider,
} from "@/lib/api/client";

const POLL_MS = 3000;
const TERMINAL: ApiDeploymentStatus[] = ["active", "failed", "cancelled"];
const isTerminal = (s: ApiDeploymentStatus) => TERMINAL.includes(s);
const enc = encodeURIComponent;
const PLANNER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  gemini: "Gemini",
  mistral: "Mistral",
};
// Every planner provider Ship can drive — offered in "manual key" mode,
// where the operator brings their own provider + key instead of relying
// on a key already configured on the repo.
const ALL_PLANNER_PROVIDERS = ["gemini", "openai", "anthropic", "mistral"];

// New-deployment wizard steps, in order. Each gate must pass before the
// stepper lets the operator advance, so they can't (e.g.) try to authorize
// GitHub before DigitalOcean is connected.
const DEPLOY_STEPS = ["repo", "digitalocean", "planner", "deploy"] as const;
type DeployStep = (typeof DEPLOY_STEPS)[number];
const DEPLOY_STEP_LABELS: Record<DeployStep, string> = {
  repo: "Repository",
  digitalocean: "Connect DigitalOcean",
  planner: "Deploy planner",
  deploy: "Deploy",
};

// DigitalOcean's "connect GitHub" entry point. Sends the operator to DO,
// which bounces to GitHub's app-authorization screen where they pick which
// repos the DigitalOcean GitHub app may read. Required for private-repo
// deploys (DO uses a `github` source that needs this grant). We can't do
// it headless — GitHub mandates the user consent — but this is one click
// to the right place.
const DO_GITHUB_INSTALL_URL = "https://cloud.digitalocean.com/apps/github/install";
const repoVisibilityUrl = (fullName: string | null) =>
  fullName ? `https://github.com/${fullName}/settings` : "https://github.com";

/**
 * Recovery panel for the "private repo, DigitalOcean can't reach it" case.
 * Offers the two real fixes: authorize DO's GitHub app, or make the repo
 * public. Used both proactively (modal, before deploy) and reactively
 * (app card, after a github_access failure).
 */
function PrivateRepoHelp({
  repoFullName,
  variant,
}: {
  repoFullName: string | null;
  variant: "warn" | "error";
}) {
  const tone =
    variant === "error"
      ? "border-red-500/30 bg-red-500/10"
      : "border-yellow-500/30 bg-yellow-500/10";
  return (
    <div className={`rounded-lg border ${tone} px-3 py-2.5 text-xs`}>
      <p className="font-semibold text-white/90">
        Private repo — DigitalOcean needs access
      </p>
      <p className="mt-1 text-white/55">
        To deploy a private repo, DigitalOcean must be authorized to read it.
        Pick one:
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <a
          href={DO_GITHUB_INSTALL_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="rounded-md bg-blue-600 px-2.5 py-1.5 text-[11px] font-semibold text-white transition hover:bg-blue-500"
        >
          Authorize on GitHub →
        </a>
        <a
          href={repoVisibilityUrl(repoFullName)}
          target="_blank"
          rel="noopener noreferrer"
          className="rounded-md border border-white/15 px-2.5 py-1.5 text-[11px] font-semibold text-white/75 transition hover:bg-white/[0.06]"
        >
          Or make it public →
        </a>
      </div>
      <p className="mt-2 text-[10px] text-white/35">
        {variant === "warn"
          ? "Already authorized? Just hit Deploy below — that's the check. If DigitalOcean still can't read the repo, you'll get a clear error to retry."
          : "Authorize (or make it public), then deploy again — the deploy itself confirms access."}
      </p>
    </div>
  );
}

function errText(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const msg = (detail as { message?: unknown }).message;
    if (typeof msg === "string") return msg;
    try {
      return JSON.stringify(detail);
    } catch {
      return `Request failed (${status})`;
    }
  }
  return `Request failed (${status})`;
}

function statusTone(s: ApiDeploymentStatus) {
  if (s === "active") return "text-emerald-400";
  if (s === "failed" || s === "cancelled") return "text-red-400";
  return "text-yellow-400";
}
function statusDot(s: ApiDeploymentStatus) {
  if (s === "active") return "bg-emerald-400";
  if (s === "failed" || s === "cancelled") return "bg-red-400";
  return "bg-yellow-400 animate-pulse";
}
function statusLabel(s: ApiDeploymentStatus, detail: string | null) {
  const map: Record<ApiDeploymentStatus, string> = {
    pending: "Pending",
    planning: "Analyzing…",
    deploying: detail ? `Building · ${detail}` : "Building…",
    active: "Active",
    failed: "Failed",
    cancelled: "Cancelled",
  };
  return map[s] ?? s;
}
function relTime(iso: string) {
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

interface AppGroup {
  key: string;
  repoId: string;
  repoName: string;
  provider: string;
  current: ApiDeployment;
  history: ApiDeployment[];
}

function groupByApp(deps: ApiDeployment[]): AppGroup[] {
  const map = new Map<string, ApiDeployment[]>();
  for (const d of deps) {
    const key = `${d.repo_id}:${d.provider}`;
    const arr = map.get(key);
    if (arr) arr.push(d);
    else map.set(key, [d]);
  }
  const groups: AppGroup[] = [];
  for (const [key, list] of map) {
    const sorted = [...list].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
    const current = sorted[0];
    groups.push({
      key,
      repoId: current.repo_id,
      repoName: current.repo_full_name ?? current.repo_id,
      provider: current.provider,
      current,
      history: sorted,
    });
  }
  groups.sort(
    (a, b) =>
      new Date(b.current.created_at).getTime() -
      new Date(a.current.created_at).getTime(),
  );
  return groups;
}

export default function DeploymentsClient({ workspaceId }: { workspaceId: string }) {
  const [deployments, setDeployments] = useState<ApiDeployment[]>([]);
  const [repos, setRepos] = useState<ApiActivatedRepo[]>([]);
  const [providers, setProviders] = useState<ApiDeployProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalRepoId, setModalRepoId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchList = useCallback(async () => {
    const res = await fetch(`/api/deployments?ws=${enc(workspaceId)}`);
    if (res.ok) {
      const data: ApiDeployment[] = await res.json();
      setDeployments(data);
      return data;
    }
    return null;
  }, [workspaceId]);

  const refreshOne = useCallback(
    async (id: string): Promise<ApiDeployment | null> => {
      try {
        const res = await fetch(`/api/deployment?ws=${enc(workspaceId)}&id=${enc(id)}`);
        if (res.ok) return (await res.json()) as ApiDeployment;
      } catch {
        /* best effort */
      }
      return null;
    },
    [workspaceId],
  );

  const pollInFlight = useCallback(
    async (current: ApiDeployment[]) => {
      const inFlight = current.filter((d) => !isTerminal(d.status));
      if (inFlight.length === 0) return;
      const refreshed = await Promise.all(inFlight.map((d) => refreshOne(d.id)));
      setDeployments((prev) =>
        prev.map((d) => refreshed.find((r) => r && r.id === d.id) ?? d),
      );
    },
    [refreshOne],
  );

  useEffect(() => {
    (async () => {
      await Promise.all([
        fetchList(),
        fetch(`/api/repos?ws=${enc(workspaceId)}`)
          .then((r) => (r.ok ? r.json() : []))
          .then((d: ApiActivatedRepo[]) => setRepos(d))
          .catch(() => setRepos([])),
        fetch(`/api/deploy/providers?ws=${enc(workspaceId)}`)
          .then((r) => (r.ok ? r.json() : []))
          .then((d: ApiDeployProvider[]) => setProviders(d))
          .catch(() => setProviders([])),
      ]);
      setLoading(false);
    })();
  }, [fetchList, workspaceId]);

  useEffect(() => {
    if (pollRef.current) clearTimeout(pollRef.current);
    if (!deployments.some((d) => !isTerminal(d.status))) return;
    pollRef.current = setTimeout(() => void pollInFlight(deployments), POLL_MS);
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [deployments, pollInFlight]);

  const handleRedeploy = (repoId: string) => {
    setModalRepoId(repoId);
    setModalOpen(true);
  };

  const handleRecheck = async (id: string) => {
    const fresh = await refreshOne(id);
    if (fresh) setDeployments((prev) => prev.map((d) => (d.id === id ? fresh : d)));
  };

  const handleDelete = async (repoId: string): Promise<boolean> => {
    const res = await fetch(`/api/deploy/teardown`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ workspaceId, repoId }),
    });
    if (res.ok) {
      // Drop every deployment row for this repo — its card disappears.
      setDeployments((prev) => prev.filter((d) => d.repo_id !== repoId));
      return true;
    }
    return false;
  };

  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const groups = groupByApp(deployments);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-white/50">
          One-click deploy to a cloud provider · DigitalOcean App Platform
        </p>
        <button
          onClick={() => {
            setModalRepoId(null);
            setModalOpen(true);
          }}
          className="rounded-full bg-blue-600 px-4 py-1.5 text-xs font-semibold text-white transition hover:bg-blue-500"
        >
          + New deployment
        </button>
      </div>

      {loading ? (
        <p className="text-xs text-white/40">Loading…</p>
      ) : groups.length === 0 ? (
        <div className="rounded-lg border border-white/10 bg-white/[0.03] px-6 py-10 text-center">
          <p className="text-sm font-medium text-white/60">No deployments yet</p>
          <p className="mt-1 text-xs text-white/30">
            Click “New deployment”, pick a repo, and deploy it.
          </p>
        </div>
      ) : (
        <div className="space-y-2.5">
          {groups.map((g) => (
            <AppCard
              key={g.key}
              workspaceId={workspaceId}
              group={g}
              expanded={expanded.has(g.key)}
              onToggle={() => toggle(g.key)}
              onRedeploy={() => handleRedeploy(g.repoId)}
              onRecheck={() => handleRecheck(g.current.id)}
              onDelete={() => handleDelete(g.repoId)}
            />
          ))}
        </div>
      )}

      {modalOpen && (
        <NewDeploymentModal
          workspaceId={workspaceId}
          repos={repos}
          providers={providers}
          initialRepoId={modalRepoId}
          onClose={() => setModalOpen(false)}
          onDeployed={(dep) => {
            setDeployments((prev) => [dep, ...prev]);
            setModalRepoId(null);
            setModalOpen(false);
          }}
          onProvidersChanged={setProviders}
        />
      )}
    </div>
  );
}

type CardTab = "overview" | "history" | "activity" | "settings";

function AppCard({
  workspaceId,
  group,
  expanded,
  onToggle,
  onRedeploy,
  onRecheck,
  onDelete,
}: {
  workspaceId: string;
  group: AppGroup;
  expanded: boolean;
  onToggle: () => void;
  onRedeploy: () => void;
  onRecheck: () => void;
  onDelete: () => Promise<boolean>;
}) {
  const [tab, setTab] = useState<CardTab>("overview");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const cur = group.current;
  const inFlight = !isTerminal(cur.status);

  const doDelete = async () => {
    setDeleting(true);
    setDeleteError(null);
    try {
      const ok = await onDelete();
      if (!ok) setDeleteError("Couldn’t delete — try again.");
    } finally {
      setDeleting(false);
    }
  };

  const redeploy = async () => {
    setBusy(true);
    try {
      onRedeploy();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="overflow-hidden rounded-xl border border-white/10 bg-white/[0.03]">
      {/* Collapsed header — click to expand */}
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-white/[0.02]"
      >
        <span
          className={`h-2.5 w-2.5 flex-shrink-0 rounded-full ${statusDot(cur.status)}`}
          title={statusLabel(cur.status, cur.status_detail)}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold text-white">
              {group.repoName}
            </span>
            <span className={`text-[11px] font-semibold ${statusTone(cur.status)}`}>
              {statusLabel(cur.status, cur.status_detail)}
            </span>
            {cur.live_url && (
              <a
                href={cur.live_url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="min-w-0 truncate text-xs text-blue-400 hover:text-blue-300"
              >
                {cur.live_url.replace(/^https?:\/\//, "")} ↗
              </a>
            )}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-[11px] text-white/40">
            <span>{group.provider}</span>
            <span>·</span>
            <span>{relTime(cur.created_at)}</span>
            {cur.live_url && cur.status === "active" && (
              <>
                <span>·</span>
                <span
                  className={
                    cur.healthy === true
                      ? "text-emerald-400"
                      : cur.healthy === false
                        ? "text-red-400"
                        : "text-yellow-400"
                  }
                >
                  {cur.healthy === true
                    ? "health ok"
                    : cur.healthy === false
                      ? "health failing"
                      : "health …"}
                </span>
              </>
            )}
          </div>
        </div>

        <span
          onClick={(e) => {
            e.stopPropagation();
            if (busy || inFlight) return;
            void redeploy();
          }}
          className={[
            "rounded-full px-3 py-1 text-[11px] font-semibold transition",
            busy || inFlight
              ? "cursor-not-allowed bg-white/10 text-white/40"
              : "cursor-pointer bg-white/10 text-white hover:bg-white/20",
          ].join(" ")}
        >
          {inFlight ? "…" : busy ? "…" : "Redeploy"}
        </span>
        <span className="text-white/30">{expanded ? "▴" : "▾"}</span>
      </button>

      {/* Expanded body */}
      {expanded && (
        <div className="border-t border-white/10 px-4 py-3">
          <nav className="mb-3 inline-flex rounded-lg border border-white/10 bg-white/[0.02] p-0.5 text-[11px] font-semibold">
            {(["overview", "history", "activity", "settings"] as CardTab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={
                  tab === t
                    ? "rounded-md bg-white/15 px-3 py-1 capitalize text-white"
                    : "rounded-md px-3 py-1 capitalize text-white/50 hover:text-white"
                }
              >
                {t}
              </button>
            ))}
          </nav>

          {tab === "overview" && (
            <dl className="space-y-2 text-xs">
              <Row label="Status">
                <span className={statusTone(cur.status)}>
                  {statusLabel(cur.status, cur.status_detail)}
                </span>
              </Row>
              {cur.live_url && (
                <Row label="URL">
                  <a
                    href={cur.live_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-400 hover:text-blue-300"
                  >
                    {cur.live_url} ↗
                  </a>
                </Row>
              )}
              {cur.status === "active" && (
                <Row label="Health">
                  <span className="flex items-center gap-2">
                    <span
                      className={`inline-block h-2 w-2 rounded-full ${
                        cur.healthy === true
                          ? "bg-emerald-400"
                          : cur.healthy === false
                            ? "bg-red-400"
                            : "bg-yellow-400"
                      }`}
                    />
                    <span className="text-white/70">
                      {cur.healthy === true
                        ? "passing"
                        : cur.healthy === false
                          ? "failing"
                          : "pending"}
                    </span>
                    <button
                      onClick={onRecheck}
                      className="text-white/40 underline hover:text-white"
                    >
                      re-check
                    </button>
                  </span>
                </Row>
              )}
              {cur.plan_summary && <Row label="Plan">{cur.plan_summary}</Row>}
              {cur.error_message && (
                <Row label="Error">
                  <span className="text-red-400">{cur.error_message}</span>
                </Row>
              )}
              {cur.error_kind === "github_access" && (
                <div className="mt-1">
                  <PrivateRepoHelp
                    repoFullName={cur.repo_full_name}
                    variant="error"
                  />
                </div>
              )}
            </dl>
          )}

          {tab === "history" && (
            <ul className="space-y-1.5">
              {group.history.map((d) => (
                <li key={d.id} className="flex items-center gap-2 text-xs">
                  <span className={`h-1.5 w-1.5 rounded-full ${statusDot(d.status)}`} />
                  <span className={`w-20 ${statusTone(d.status)}`}>
                    {statusLabel(d.status, null)}
                  </span>
                  <span className="text-white/40">{relTime(d.created_at)}</span>
                  {d.error_message && (
                    <span className="truncate text-white/40">· {d.error_message}</span>
                  )}
                </li>
              ))}
            </ul>
          )}

          {tab === "activity" && (
            <ActivityFeed workspaceId={workspaceId} repoId={group.repoId} />
          )}

          {tab === "settings" && (
            <div className="space-y-4">
              <p className="text-xs text-white/40">
                Environment variables &amp; domains will live here (secrets gate
                is on the roadmap).
              </p>
              <div className="rounded-lg border border-red-500/20 bg-red-500/[0.04] p-3">
                <div className="text-xs font-semibold text-white/80">
                  Delete deployment
                </div>
                <p className="mt-0.5 text-[11px] text-white/45">
                  Permanently removes this app from DigitalOcean and stops all
                  billing. This can’t be undone.
                </p>
                {deleteError && (
                  <p className="mt-2 text-[11px] text-red-400">{deleteError}</p>
                )}
                <div className="mt-2 flex items-center gap-2">
                  {!confirmDelete ? (
                    <button
                      onClick={() => setConfirmDelete(true)}
                      className="rounded-md border border-red-500/40 px-3 py-1 text-[11px] font-semibold text-red-300 transition hover:bg-red-500/10"
                    >
                      Delete
                    </button>
                  ) : (
                    <>
                      <button
                        onClick={doDelete}
                        disabled={deleting}
                        className="rounded-md bg-red-600 px-3 py-1 text-[11px] font-semibold text-white transition hover:bg-red-500 disabled:opacity-50"
                      >
                        {deleting ? "Deleting…" : "Yes, delete it"}
                      </button>
                      <button
                        onClick={() => setConfirmDelete(false)}
                        disabled={deleting}
                        className="rounded-md px-3 py-1 text-[11px] font-semibold text-white/50 hover:text-white"
                      >
                        Cancel
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <dt className="w-16 flex-shrink-0 text-white/35">{label}</dt>
      <dd className="min-w-0 flex-1 break-words text-white/80">{children}</dd>
    </div>
  );
}

interface ActivityEvent {
  kind: string;
  message: string;
  created_at: string;
}

function eventDot(kind: string) {
  if (kind === "deployed" || kind === "health_restored") return "bg-emerald-400";
  if (kind === "deploy_failed" || kind === "removed_externally" || kind === "health_lost")
    return "bg-red-400";
  if (kind === "deleted") return "bg-white/40";
  return "bg-blue-400";
}

function ActivityFeed({ workspaceId, repoId }: { workspaceId: string; repoId: string }) {
  const [events, setEvents] = useState<ActivityEvent[] | null>(null);
  useEffect(() => {
    let alive = true;
    fetch(`/api/deploy/events?ws=${enc(workspaceId)}&repoId=${enc(repoId)}`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d: ActivityEvent[]) => alive && setEvents(d))
      .catch(() => alive && setEvents([]));
    return () => {
      alive = false;
    };
  }, [workspaceId, repoId]);

  if (events === null) return <p className="text-xs text-white/40">Loading…</p>;
  if (events.length === 0)
    return (
      <p className="text-xs text-white/40">No activity recorded yet.</p>
    );
  return (
    <ul className="space-y-2">
      {events.map((e, i) => (
        <li key={i} className="flex items-start gap-2 text-xs">
          <span className={`mt-1 h-1.5 w-1.5 flex-shrink-0 rounded-full ${eventDot(e.kind)}`} />
          <div className="min-w-0 flex-1">
            <span className="text-white/80">{e.message}</span>
            <span className="ml-2 text-[10px] text-white/30">
              {relTime(e.created_at)}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function NewDeploymentModal({
  workspaceId,
  repos,
  providers,
  initialRepoId,
  onClose,
  onDeployed,
  onProvidersChanged,
}: {
  workspaceId: string;
  repos: ApiActivatedRepo[];
  providers: ApiDeployProvider[];
  initialRepoId: string | null;
  onClose: () => void;
  onDeployed: (dep: ApiDeployment) => void;
  onProvidersChanged: (p: ApiDeployProvider[]) => void;
}) {
  const [repoId, setRepoId] = useState(initialRepoId ?? repos[0]?.id ?? "");
  const [plannerProviders, setPlannerProviders] = useState<string[]>([]);
  const [plannerProvider, setPlannerProvider] = useState("");
  const [plannerModel, setPlannerModel] = useState("");
  const [plannerLoading, setPlannerLoading] = useState(false);
  // Model dropdown — populated live from the provider's list-models API
  // (services.deploy.model_catalog). ``""`` means "use Ship's default".
  const [plannerModels, setPlannerModels] = useState<string[]>([]);
  const [plannerModelDefault, setPlannerModelDefault] = useState("");
  const [plannerModelsSource, setPlannerModelsSource] = useState<string>("");
  const [plannerModelsLoading, setPlannerModelsLoading] = useState(false);
  // Plaintext provider key pasted here to pull a live model list (repo
  // GitHub-secret keys can't be read back). Sent with the deploy so the
  // planner actually uses it; never persisted by Ship.
  const [plannerApiKey, setPlannerApiKey] = useState("");
  // Manual-LLM mode: bring your own provider + key (e.g. Anthropic) instead
  // of the repo's configured key. Hidden behind a toggle so the common
  // case stays a clean provider + model picker.
  const [manualLlm, setManualLlm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Wizard step index into DEPLOY_STEPS.
  const [step, setStep] = useState(0);

  const doProvider = providers.find((p) => p.provider === "digitalocean");
  const connected = doProvider?.connected ?? false;
  const selectedRepo = repos.find((r) => r.id === repoId) ?? null;
  const selectedPrivate = selectedRepo?.private ?? false;
  // Saved per-repo planner preference — prefills the picker and is the
  // baseline the persist-on-change guard compares against.
  const prefProvider = selectedRepo?.deploy_planner_provider ?? "";
  const prefModel = selectedRepo?.deploy_planner_model ?? "";
  // Only persist the preference once the operator actually touches the
  // picker — opening the modal shouldn't silently write a default.
  const plannerTouched = useRef(false);
  const secretsHref = repoId
    ? `/onboarding?step=roles&ws=${enc(workspaceId)}`
    : `/onboarding?step=roles&ws=${enc(workspaceId)}`;

  // Sync repoId once repos load. The modal can mount before /api/repos
  // resolves (repoId === ""), which leaves the <select> visually showing
  // the first option while state stays empty — so the Next gate (!!repoId)
  // would wrongly stay disabled. Point it at a real repo as soon as we can.
  useEffect(() => {
    if (repos.length === 0) return;
    setRepoId((cur) => {
      if (cur && repos.some((r) => r.id === cur)) return cur;
      if (initialRepoId && repos.some((r) => r.id === initialRepoId))
        return initialRepoId;
      return repos[0].id;
    });
  }, [repos, initialRepoId]);

  useEffect(() => {
    if (!repoId) {
      setPlannerProviders([]);
      setPlannerProvider("");
      return;
    }
    let alive = true;
    setPlannerLoading(true);
    setError(null);
    fetch(`/api/deploy/planner-options?ws=${enc(workspaceId)}&repoId=${enc(repoId)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: { providers: string[] }) => {
        if (!alive) return;
        const list = data.providers;
        setPlannerProviders(list);
        // Seed from the saved per-repo preference when its provider is
        // available; else keep a still-valid prior pick; else first.
        const chosen =
          prefProvider && list.includes(prefProvider)
            ? prefProvider
            : plannerProvider && list.includes(plannerProvider)
              ? plannerProvider
              : (list[0] ?? "");
        plannerTouched.current = false;
        setPlannerProvider(chosen);
        // Prefill the model only when it belongs to the chosen provider.
        setPlannerModel(chosen && chosen === prefProvider ? prefModel : "");
        setPlannerApiKey("");
      })
      .catch((err) => {
        if (alive) setError(err instanceof Error ? err.message : "planner check failed");
      })
      .finally(() => {
        if (alive) setPlannerLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, workspaceId]);

  // Persist the preference back to the repo when the operator changes the
  // provider/model — debounced, and only after a real interaction, so the
  // next deploy prefills their last choice. Skips no-op writes.
  useEffect(() => {
    if (!repoId || !plannerProvider || !plannerTouched.current) return;
    if (plannerProvider === prefProvider && plannerModel.trim() === prefModel)
      return;
    const t = setTimeout(() => {
      fetch(`/api/deploy/planner-pref`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ws: workspaceId,
          repoId,
          provider: plannerProvider || null,
          model: plannerModel.trim() || null,
        }),
      }).catch(() => {
        // Non-fatal: the deploy call also persists the effective choice.
      });
    }, 600);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, workspaceId, plannerProvider, plannerModel]);

  // Load the model list for the selected provider. Re-fetches when the
  // provider or the pasted key changes (debounced so typing a key doesn't
  // fire a request per keystroke). Pasting a valid key flips the list from
  // the curated fallback to the provider's live catalogue.
  useEffect(() => {
    if (!repoId || !plannerProvider) {
      setPlannerModels([]);
      setPlannerModelDefault("");
      setPlannerModelsSource("");
      return;
    }
    let alive = true;
    const timer = setTimeout(() => {
      setPlannerModelsLoading(true);
      fetch(`/api/deploy/planner-models`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ws: workspaceId,
          repoId,
          provider: plannerProvider,
          apiKey: plannerApiKey.trim() || undefined,
        }),
      })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((data: { models: string[]; default_model: string; source: string }) => {
          if (!alive) return;
          setPlannerModels(data.models ?? []);
          setPlannerModelDefault(data.default_model ?? "");
          setPlannerModelsSource(data.source ?? "");
        })
        .catch(() => {
          if (!alive) return;
          setPlannerModels([]);
          setPlannerModelDefault("");
          setPlannerModelsSource("error");
        })
        .finally(() => {
          if (alive) setPlannerModelsLoading(false);
        });
    }, plannerApiKey.trim() ? 400 : 0);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [repoId, workspaceId, plannerProvider, plannerApiKey]);

  // Re-pull DigitalOcean connection status. Called when the tab regains
  // focus so the wizard updates after the operator finishes connecting in
  // the new tab we opened.
  const refreshProviders = useCallback(async () => {
    try {
      const r = await fetch(`/api/deploy/providers?ws=${enc(workspaceId)}`);
      if (r.ok) onProvidersChanged((await r.json()) as ApiDeployProvider[]);
    } catch {
      /* best effort */
    }
  }, [workspaceId, onProvidersChanged]);

  // Re-pull which planner providers have a key on this repo (a key added
  // in another tab — GitHub secret / Roles — shows up without a reload).
  // Only updates the available list; never clobbers the operator's pick.
  const refreshPlannerOptions = useCallback(async () => {
    if (!repoId) return;
    try {
      const r = await fetch(
        `/api/deploy/planner-options?ws=${enc(workspaceId)}&repoId=${enc(repoId)}`,
      );
      if (!r.ok) return;
      const data = (await r.json()) as { providers: string[] };
      setPlannerProviders(data.providers);
      setPlannerProvider((prev) =>
        prev && data.providers.includes(prev) ? prev : (data.providers[0] ?? ""),
      );
    } catch {
      /* best effort */
    }
  }, [repoId, workspaceId]);

  // We open DigitalOcean / GitHub in a NEW TAB (a full-page redirect would
  // drop the wizard's step + selections). When the operator finishes there
  // and returns to this tab, refresh the state that may have changed.
  useEffect(() => {
    const onVisible = () => {
      if (document.hidden) return;
      void refreshProviders();
      void refreshPlannerOptions();
    };
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refreshProviders, refreshPlannerOptions]);

  const handleConnect = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/deploy/connect`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ workspaceId }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(errText(body, res.status));
        return;
      }
      const data: { install_url: string } = await res.json();
      // New tab so this wizard (step + repo + planner choice) survives.
      // On return, the focus listener flips DigitalOcean to "Connected".
      window.open(data.install_url, "_blank", "noopener,noreferrer");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleDeploy = async () => {
    if (!repoId) {
      setError("Pick a repository first.");
      return;
    }
    if (plannerProviders.length === 0 || !plannerProvider) {
      setError("Add an LLM API key for this repo before deploying.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/deploy`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspaceId,
          repoId,
          llmProvider: plannerProvider || undefined,
          llmModel: plannerModel.trim() || undefined,
          llmApiKey: plannerApiKey.trim() || undefined,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        if (res.status === 409) {
          onProvidersChanged(
            providers.map((p) =>
              p.provider === "digitalocean" ? { ...p, connected: false } : p,
            ),
          );
        }
        setError(errText(body, res.status));
        return;
      }
      const dep: ApiDeployment = await res.json();
      onDeployed(dep);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const stepKey = DEPLOY_STEPS[step];
  const isLastStep = step === DEPLOY_STEPS.length - 1;
  // Planner is satisfied either by a key configured on the repo (repo mode)
  // or by a pasted key (manual mode).
  const plannerReady = manualLlm
    ? plannerApiKey.trim().length > 0 && !!plannerProvider
    : plannerProviders.length > 0;
  // Gate: may we move past the current step?
  const canAdvance =
    stepKey === "repo"
      ? repos.length > 0 && !!repoId
      : stepKey === "digitalocean"
        ? connected
        : stepKey === "planner"
          ? plannerReady
          : true;
  const deployReady = connected && plannerReady && !!repoId;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-white/10 bg-[#0e1015] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-white">New deployment</h2>
          <button onClick={onClose} className="text-white/40 transition hover:text-white" aria-label="Close">
            ✕
          </button>
        </div>

        {/* Progress bar — one segment per step, filled up to the current. */}
        <div className="mb-2 flex items-center gap-1.5">
          {DEPLOY_STEPS.map((s, i) => (
            <div
              key={s}
              className={`h-1 flex-1 rounded-full ${
                i <= step ? "bg-blue-500" : "bg-white/10"
              }`}
            />
          ))}
        </div>
        <p className="mb-4 text-[11px] font-semibold uppercase tracking-wider text-white/45">
          Step {step + 1} of {DEPLOY_STEPS.length} · {DEPLOY_STEP_LABELS[stepKey]}
        </p>

        {/* ── Step 1 · Repository ─────────────────────────────── */}
        {stepKey === "repo" && (
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-white/45">
              Repository
            </label>
            {repos.length === 0 ? (
              <p className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-white/50">
                No repos connected to this workspace yet.
              </p>
            ) : (
              <select
                value={repoId}
                onChange={(e) => setRepoId(e.target.value)}
                className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none"
              >
                {repos.map((r) => (
                  <option key={r.id} value={r.id} className="bg-[#0e1015]">
                    {r.full_name}
                    {r.private ? " (private)" : ""}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        {/* ── Step 2 · Connect DigitalOcean ───────────────────── */}
        {stepKey === "digitalocean" && (
          <div>
            <div className="mb-3 flex items-center justify-between rounded-lg border border-blue-500/40 bg-blue-500/10 px-3 py-2">
              <span className="text-sm font-medium text-white">DigitalOcean</span>
              <span
                className={`text-[10px] font-semibold ${connected ? "text-emerald-400" : "text-yellow-400"}`}
              >
                {connected ? "Connected" : "Not connected"}
              </span>
            </div>
            {connected ? (
              <p className="text-xs text-emerald-400/80">
                ✓ DigitalOcean is connected. Continue to the next step.
              </p>
            ) : (
              <>
                <p className="mb-2 text-xs text-white/55">
                  Ship deploys to your DigitalOcean account. Connect it once —
                  opens in a new tab; come back here when you&apos;re done.
                </p>
                <button
                  onClick={handleConnect}
                  disabled={busy}
                  className="w-full rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-500 disabled:opacity-50"
                >
                  {busy ? "Opening…" : "Connect DigitalOcean ↗"}
                </button>
                <p className="mt-2 text-center text-[11px] text-white/40">
                  Already connected in the other tab?{" "}
                  <button
                    type="button"
                    onClick={() => void refreshProviders()}
                    className="underline underline-offset-2 transition hover:text-white"
                  >
                    Re-check ↻
                  </button>
                </p>
              </>
            )}
          </div>
        )}

        {/* ── Step 3 · Deploy planner ─────────────────────────── */}
        {stepKey === "planner" && (
          <div className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-white/45">
              Deploy planner
            </div>
            {plannerLoading ? (
              <p className="mt-1 text-xs text-white/55">Checking LLM keys…</p>
            ) : (
              <div className="mt-2 grid gap-2">
                {!manualLlm && plannerProviders.length === 0 ? (
                  <p className="text-xs text-amber-200">
                    No LLM API key found for this repo.{" "}
                    <a
                      href={secretsHref}
                      target="_blank"
                      rel="noreferrer"
                      className="underline hover:text-amber-100"
                    >
                      Add one in Roles ↗
                    </a>{" "}
                    — or use a manual key below.
                  </p>
                ) : (
                  <>
                    <select
                      value={plannerProvider}
                      onChange={(e) => {
                        const v = e.target.value;
                        plannerTouched.current = true;
                        setPlannerProvider(v);
                        setPlannerApiKey("");
                        setPlannerModel(v === prefProvider ? prefModel : "");
                      }}
                      className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none"
                    >
                      {(manualLlm ? ALL_PLANNER_PROVIDERS : plannerProviders).map(
                        (provider) => (
                          <option
                            key={provider}
                            value={provider}
                            className="bg-[#0e1015]"
                          >
                            {PLANNER_LABELS[provider] ?? provider}
                          </option>
                        ),
                      )}
                    </select>
                    {manualLlm && (
                      <input
                        type="password"
                        value={plannerApiKey}
                        onChange={(e) => setPlannerApiKey(e.target.value)}
                        placeholder={`${PLANNER_LABELS[plannerProvider] ?? plannerProvider} API key — required`}
                        autoComplete="off"
                        className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white placeholder:text-white/25 focus:border-blue-500 focus:outline-none"
                      />
                    )}
                    {/* Combobox: pick a suggestion or type any id by hand (free-text
                        fallback if models.dev is down or a model is too new). */}
                    <input
                      type="text"
                      list="planner-model-options"
                      value={plannerModel}
                      onChange={(e) => {
                        plannerTouched.current = true;
                        setPlannerModel(e.target.value);
                      }}
                      placeholder={
                        plannerModelsLoading
                          ? "Loading models…"
                          : `Default${plannerModelDefault ? ` (${plannerModelDefault})` : ""} — or type a model id`
                      }
                      autoComplete="off"
                      spellCheck={false}
                      className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white placeholder:text-white/25 focus:border-blue-500 focus:outline-none"
                    />
                    <datalist id="planner-model-options">
                      {plannerModels.map((m) => (
                        <option key={m} value={m} />
                      ))}
                    </datalist>
                    {!plannerModelsLoading && plannerModelsSource === "live" && (
                      <p className="text-[11px] leading-snug text-emerald-400/70">
                        Live list from{" "}
                        {PLANNER_LABELS[plannerProvider] ?? plannerProvider}.
                      </p>
                    )}
                    {!plannerModelsLoading && plannerModelsSource === "catalog" && (
                      <p className="text-[11px] leading-snug text-white/40">
                        Models from the public catalogue. Pick one, or type an
                        id by hand.
                      </p>
                    )}
                    {!plannerModelsLoading &&
                      plannerModelsSource &&
                      plannerModelsSource !== "live" &&
                      plannerModelsSource !== "catalog" && (
                        <p className="text-[11px] leading-snug text-amber-200/80">
                          Couldn&apos;t load the model list. Type the model id by
                          hand, or leave blank for the default.
                        </p>
                      )}
                  </>
                )}

                {/* Toggle: repo-configured key ↔ bring-your-own key. */}
                <button
                  type="button"
                  onClick={() => {
                    const next = !manualLlm;
                    plannerTouched.current = true;
                    setManualLlm(next);
                    setPlannerModel("");
                    if (next) {
                      setPlannerProvider((p) => p || ALL_PLANNER_PROVIDERS[0]);
                    } else {
                      setPlannerApiKey("");
                      setPlannerProvider(plannerProviders[0] ?? "");
                    }
                  }}
                  className="mt-1 self-start text-[11px] text-white/45 underline underline-offset-2 transition hover:text-white"
                >
                  {manualLlm
                    ? "← Use the key configured on the repo"
                    : "Use a manual LLM key (bring your own) →"}
                </button>
              </div>
            )}
          </div>
        )}

        {/* ── Step 4 · Deploy ─────────────────────────────────── */}
        {stepKey === "deploy" && (
          <div className="grid gap-3">
            <div className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-white/70">
              <div className="flex justify-between gap-2">
                <span className="text-white/45">Repository</span>
                <span className="truncate text-white">
                  {selectedRepo?.full_name ?? "—"}
                </span>
              </div>
              <div className="mt-1 flex justify-between gap-2">
                <span className="text-white/45">Planner</span>
                <span className="text-white">
                  {(PLANNER_LABELS[plannerProvider] ?? plannerProvider) || "—"}
                  {" · "}
                  {plannerModel.trim() ||
                    `default${plannerModelDefault ? ` (${plannerModelDefault})` : ""}`}
                </span>
              </div>
            </div>
            {selectedPrivate && (
              <PrivateRepoHelp
                repoFullName={selectedRepo?.full_name ?? null}
                variant="warn"
              />
            )}
          </div>
        )}

        {error && (
          <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}

        {/* ── Footer · Back / Next / Deploy ───────────────────── */}
        <div className="mt-5 flex items-center justify-between gap-3">
          <button
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0 || busy}
            className="rounded-lg px-3 py-2 text-xs font-semibold text-white/60 transition hover:text-white disabled:cursor-not-allowed disabled:opacity-30"
          >
            ← Back
          </button>
          {isLastStep ? (
            <button
              onClick={handleDeploy}
              disabled={busy || !deployReady}
              className={[
                "rounded-lg px-5 py-2.5 text-sm font-semibold transition",
                busy || !deployReady
                  ? "cursor-not-allowed bg-white/10 text-white/40"
                  : "bg-blue-600 text-white hover:bg-blue-500",
              ].join(" ")}
            >
              {busy ? "Submitting…" : "Deploy"}
            </button>
          ) : (
            <button
              onClick={() => canAdvance && setStep((s) => s + 1)}
              disabled={!canAdvance}
              className={[
                "rounded-lg px-5 py-2.5 text-sm font-semibold transition",
                !canAdvance
                  ? "cursor-not-allowed bg-white/10 text-white/40"
                  : "bg-blue-600 text-white hover:bg-blue-500",
              ].join(" ")}
            >
              Next →
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
