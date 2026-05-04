/**
 * Per-repo Ship-managed Actions secrets page (B10).
 *
 * Server Component that lists the workspace's Ship-managed secrets for
 * a single activated repo, shows a required-but-missing matrix
 * (pulled from pipeline catalog metadata), and offers plain native
 * <form> affordances to add / rotate / delete — no client JS
 * required, because onboarding tenants reach this page with a fresh
 * cookie and no bundle cache warm.
 *
 * Routing: ``/repos/{repo_id}/secrets``. The repo-id route segment
 * is enough because our API client scopes everything by the current
 * workspace (derived from the session), so we don't need to
 * duplicate ``ws`` in the URL. Admins land here via the dashboard
 * repo card's "Manage secrets →" link; direct links from
 * notifications / email are safe too.
 */

import { cookies } from "next/headers";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { PageBody, PageHeader } from "@/components/app-shell";
import { ApiUnavailable } from "@/components/api-unavailable";
import {
  Badge,
  Card,
  CardHeader,
  LiveBanner,
  type BadgeTone,
} from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listActivatedRepos,
  listRepoSecrets,
  listRequiredSecrets,
  type ApiActivatedRepo,
  type ApiRepoSecret,
  type ApiRequiredSecret,
} from "@/lib/api/client";
import type { ApiWorkspace } from "@/lib/api/types";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";

export const dynamic = "force-dynamic";

type PageProps = {
  params: Promise<{ id: string }>;
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

type Mode =
  | {
      source: "live";
      workspace: ApiWorkspace;
      repo: ApiActivatedRepo;
      secrets: ApiRepoSecret[];
      required: ApiRequiredSecret[];
    }
  | { source: "mock"; reason: string };

async function load(repoId: string): Promise<Mode> {
  if (!isApiConfigured()) {
    return { source: "mock", reason: "SHIP_API_URL not set" };
  }
  const token = await getCachedSessionToken();
  if (!token) {
    return { source: "mock", reason: "Sign in to manage secrets" };
  }
  try {
    const workspaces = await getCachedWorkspaces();
    if (workspaces.length === 0) {
      return { source: "mock", reason: "No workspace to manage secrets for" };
    }
    const workspace = workspaces[0];
    // Looking up the repo via the activated list keeps the page a
    // one-stop lookup — no extra single-repo GET endpoint to add and
    // we get the human-readable `full_name` for the header for
    // free. 404 falls through to Next's notFound() below.
    const repos = await listActivatedRepos(workspace.id, token);
    const repo = repos.find((r) => r.id === repoId);
    if (!repo) {
      return {
        source: "mock",
        reason:
          `Repo ${repoId} is not activated for this workspace. Activate it first.`,
      };
    }
    // Fan out the two reads in parallel: the plaintext-free list of
    // stored secrets and the catalog-derived required matrix.
    const [secretsPage, requiredPage] = await Promise.all([
      listRepoSecrets(workspace.id, repo.id, token),
      listRequiredSecrets(workspace.id, repo.id, token),
    ]);
    return {
      source: "live",
      workspace,
      repo,
      secrets: secretsPage.items,
      required: requiredPage.items,
    };
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return { source: "mock", reason: "Backend is unreachable right now" };
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        redirect("/login?error=session_expired");
      }
      if (err.status === 404) {
        notFound();
      }
      return { source: "mock", reason: `API returned ${err.status}` };
    }
    throw err;
  }
}

const SYNC_TONES: Record<string, BadgeTone> = {
  synced: "ok",
  pending: "info",
  stale: "warn",
  error: "err",
};

function SyncBadge({ status }: { status: string }) {
  const tone = SYNC_TONES[status] ?? "neutral";
  return <Badge tone={tone}>{status}</Badge>;
}

function formatTs(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function reasonMessage(reason: string | null): string | null {
  if (!reason) return null;
  switch (reason) {
    case "created":
      return "Secret saved and synced to GitHub.";
    case "rotated":
      return "Secret rotated — GitHub now has the new value.";
    case "deleted":
      return "Secret deleted from Ship and GitHub.";
    case "sync_error":
      return "Saved locally, but GitHub rejected the sync. Re-try from the row.";
    case "bad_name":
      return "Name must match A-Z, 0-9, _ (uppercase) and not start with GITHUB_.";
    case "empty_value":
      return "Secret value can't be empty.";
    case "too_large":
      return "Secret exceeds GitHub's 48KB limit.";
    case "api_unavailable":
      return "Backend is unreachable — try again shortly.";
    case "forbidden":
      return "Only admins can manage secrets for this workspace.";
    case "missing_install":
      return "GitHub App installation is missing or suspended — reinstall.";
    case "not_found":
      return "That secret no longer exists (maybe removed in another tab).";
    default:
      return `Couldn't complete the action (${reason}).`;
  }
}

export default async function RepoSecretsPage(props: PageProps) {
  const { id: repoId } = await props.params;
  const search = (await props.searchParams) ?? {};
  const mode = await load(repoId);
  const banner = (search.banner ?? "").toString() || null;
  const reason = (search.reason ?? "").toString() || null;

  return (
    <>
      <PageHeader
        title="Repo secrets"
        kicker={mode.source === "live" ? mode.repo.full_name : "Secrets"}
      />
      <PageBody>
        <div className="space-y-6">
        {mode.source === "mock" ? (
          <ApiUnavailable scope="repo secrets" details={mode.reason} />
        ) : (
          <LiveBanner workspace={mode.workspace.name} />
        )}

        {banner && reasonMessage(reason) && (
          <Card
            className={
              banner === "ok"
                ? "border-aqua/40 bg-aqua/10"
                : banner === "warn"
                  ? "border-amber-400/40 bg-amber-400/10"
                  : "border-coral/40 bg-coral/10"
            }
          >
            <div className="text-sm">{reasonMessage(reason)}</div>
          </Card>
        )}

        {mode.source === "live" ? (
          <>
            <Header repo={mode.repo} />
            <RequiredMatrix
              required={mode.required}
              workspaceId={mode.workspace.id}
              repoId={mode.repo.id}
            />
            <AddSecretCard
              workspaceId={mode.workspace.id}
              repoId={mode.repo.id}
            />
            <StoredSecretsCard
              workspaceId={mode.workspace.id}
              repoId={mode.repo.id}
              secrets={mode.secrets}
            />
          </>
        ) : (
          <Card>
            <div className="p-6 text-sm text-white/70">
              Secrets management is unavailable — see banner above.
            </div>
          </Card>
        )}
        </div>
      </PageBody>
    </>
  );
}

function Header({ repo }: { repo: ApiActivatedRepo }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.25em] text-white/50">
          Secrets · {repo.full_name}
        </div>
        <h1 className="text-2xl font-bold text-white">Ship-managed Actions secrets</h1>
        <p className="mt-1 max-w-2xl text-sm text-white/65">
          Stored encrypted at rest and mirrored to GitHub Actions so{" "}
          <code className="text-white/80">${"{{ secrets.NAME }}"}</code>{" "}
          works in every workflow trigger — ``workflow_dispatch``,
          ``push``, ``pull_request``, and ``schedule:`` cron runs alike.
          Plaintext is only visible at submission time; we never show
          it back.
        </p>
      </div>
      <Link
        href="/"
        className="text-xs font-semibold text-white/55 hover:text-aqua"
      >
        ← Back to dashboard
      </Link>
    </div>
  );
}

function RequiredMatrix({
  required,
  workspaceId: _workspaceId,
  repoId: _repoId,
}: {
  required: ApiRequiredSecret[];
  workspaceId: string;
  repoId: string;
}) {
  if (required.length === 0) {
    // Keeping this short and hopeful: "nothing required" is the
    // pilot's happy path (no workflow declares required_secrets
    // yet), but the card itself has to render so operators
    // understand the matrix isn't missing — it's empty by design.
    return (
      <Card>
        <CardHeader
          title="Required by installed lanes"
          subtitle="Pipelines bound to this repo don't declare required secrets yet."
        />
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader
        title="Required by installed lanes"
        subtitle="Secrets your enabled + disabled pipelines expect to read at runtime. Missing rows will cause the lane to fail at first run — add them below."
      />
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-[10px] font-semibold uppercase tracking-[0.2em] text-white/45">
            <tr>
              <th className="px-4 py-2">Secret</th>
              <th className="px-4 py-2">Required by</th>
              <th className="px-4 py-2">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5 text-white/85">
            {required.map((r) => (
              <tr key={r.name}>
                <td className="px-4 py-2 font-mono">{r.name}</td>
                <td className="px-4 py-2">
                  {r.required_by.map((k) => (
                    <Badge key={k} tone="neutral">
                      {k}
                    </Badge>
                  ))}
                </td>
                <td className="px-4 py-2">
                  {r.stored ? (
                    r.sync_status === "synced" ? (
                      <Badge tone="ok">stored · synced</Badge>
                    ) : (
                      <Badge tone="warn">stored · {r.sync_status ?? "pending"}</Badge>
                    )
                  ) : (
                    <Badge tone="err">missing</Badge>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function AddSecretCard({
  workspaceId,
  repoId,
}: {
  workspaceId: string;
  repoId: string;
}) {
  return (
    <Card>
      <CardHeader
        title="Add or rotate a secret"
        subtitle="Names are case-insensitive on submit but stored uppercase (GitHub's convention). Re-submitting an existing name rotates the value."
      />
      <form
        action="/api/repo-secrets/upsert"
        method="POST"
        className="grid grid-cols-1 gap-3 px-4 pb-4 sm:grid-cols-2"
      >
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="repo_id" value={repoId} />
        <label className="col-span-1 flex flex-col gap-1">
          <span className="text-[11px] font-semibold uppercase tracking-[0.15em] text-white/50">
            Name
          </span>
          <input
            type="text"
            name="name"
            required
            autoComplete="off"
            pattern="[A-Za-z_][A-Za-z0-9_]*"
            placeholder="ANTHROPIC_API_KEY"
            className="rounded-md border border-white/15 bg-black/30 px-3 py-2 font-mono text-sm text-white placeholder:text-white/35 focus:border-aqua/60 focus:outline-none"
          />
        </label>
        <label className="col-span-1 flex flex-col gap-1">
          <span className="text-[11px] font-semibold uppercase tracking-[0.15em] text-white/50">
            Description (optional)
          </span>
          <input
            type="text"
            name="description"
            autoComplete="off"
            maxLength={512}
            placeholder="Claude key used by the review bot"
            className="rounded-md border border-white/15 bg-black/30 px-3 py-2 text-sm text-white placeholder:text-white/35 focus:border-aqua/60 focus:outline-none"
          />
        </label>
        <label className="col-span-1 flex flex-col gap-1 sm:col-span-2">
          <span className="text-[11px] font-semibold uppercase tracking-[0.15em] text-white/50">
            Value
          </span>
          <textarea
            name="value"
            required
            rows={4}
            autoComplete="off"
            spellCheck={false}
            placeholder="paste the secret here — we'll never show it back"
            className="rounded-md border border-white/15 bg-black/30 px-3 py-2 font-mono text-sm text-white placeholder:text-white/35 focus:border-aqua/60 focus:outline-none"
          />
        </label>
        <div className="col-span-1 flex items-center justify-end gap-3 sm:col-span-2">
          <p className="text-[11px] text-white/45">
            Stored encrypted + pushed to GitHub Actions secrets via the
            installation token.
          </p>
          <button
            type="submit"
            className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-1.5 text-[11px] font-bold text-ink shadow-glow transition hover:brightness-110"
          >
            Save secret
          </button>
        </div>
      </form>
    </Card>
  );
}

function StoredSecretsCard({
  workspaceId,
  repoId,
  secrets,
}: {
  workspaceId: string;
  repoId: string;
  secrets: ApiRepoSecret[];
}) {
  return (
    <Card>
      <CardHeader
        title={`Stored secrets (${secrets.length})`}
        subtitle="Plaintext is never returned by the API. Rotate by re-submitting the same name above; delete to remove it from both Ship and GitHub."
      />
      {secrets.length === 0 ? (
        <div className="px-4 pb-4 text-xs text-white/60">
          No secrets stored yet.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] font-semibold uppercase tracking-[0.2em] text-white/45">
              <tr>
                <th className="px-4 py-2">Name</th>
                <th className="px-4 py-2">Hint</th>
                <th className="px-4 py-2">Description</th>
                <th className="px-4 py-2">Sync</th>
                <th className="px-4 py-2">Last synced</th>
                <th className="px-4 py-2">Updated</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-white/85">
              {secrets.map((s) => (
                <tr key={s.id}>
                  <td className="px-4 py-2 font-mono">{s.name}</td>
                  <td className="px-4 py-2 font-mono text-white/60">
                    {s.masked_hint ? `••••••${s.masked_hint}` : "—"}
                  </td>
                  <td className="px-4 py-2 text-white/65">
                    {s.description ?? "—"}
                  </td>
                  <td className="px-4 py-2">
                    <SyncBadge status={s.sync_status} />
                    {s.sync_error && (
                      <div className="mt-1 max-w-xs text-[10px] text-coral/80">
                        {s.sync_error}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2 text-white/60">
                    {formatTs(s.last_synced_at)}
                  </td>
                  <td className="px-4 py-2 text-white/60">
                    {formatTs(s.updated_at)}
                  </td>
                  <td className="px-4 py-2">
                    <form
                      action="/api/repo-secrets/delete"
                      method="POST"
                      className="inline"
                    >
                      <input type="hidden" name="ws" value={workspaceId} />
                      <input type="hidden" name="repo_id" value={repoId} />
                      <input type="hidden" name="secret_id" value={s.id} />
                      <button
                        type="submit"
                        className="inline-flex items-center rounded-full border border-coral/40 bg-coral/10 px-3 py-1 text-[11px] font-semibold text-coral hover:bg-coral/20"
                        title={`Delete ${s.name} from Ship and GitHub`}
                      >
                        Delete
                      </button>
                    </form>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// Touch ``cookies()`` so Next.js treats this as dynamic — the secrets
// table is highly session-sensitive and we never want it cached in
// the ISR layer.
void cookies;
