import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
} from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listActivatedRepos,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import type { ApiActivatedRepo } from "@/lib/api/client";
import type { ApiWorkspace } from "@/lib/api/types";
import { findRepoBySlug, slugFromSegments } from "@/lib/repo-slug";

/**
 * Repo home (``/r/<owner>/<repo>``).
 *
 * PR-1 scope: render the repo-mode shell with the per-repo sidebar
 * (Lanes / Requests / Clarifications / …) and a "Now vs Trends"
 * placeholder explaining that the rich dashboard (migrated from
 * today's ``/``) lands in PR-4. The shell itself is the deliverable
 * — operators can navigate to every repo surface from here, and
 * shared links (``/r/acme/api``) now pin scope to the URL.
 */

export const dynamic = "force-dynamic";

type Params = { slug?: string[] };

export default async function RepoHomePage({
  params,
}: {
  params: Promise<Params>;
}) {
  const resolved = await params;
  const slug = slugFromSegments(resolved.slug);
  if (!slug) notFound();

  if (!isApiConfigured()) {
    return renderMock(slug);
  }

  const token = await getSessionToken();
  if (!token) {
    redirect(`/login?next=${encodeURIComponent(`/r/${slug}`)}`);
  }

  const ctx = await loadContext(token, slug);
  if (ctx === "unauthorized") {
    redirect(`/login?next=${encodeURIComponent(`/r/${slug}`)}`);
  }
  if (ctx === "down") return renderDownState(slug);
  if (ctx === "empty") redirect("/onboarding?step=github");
  if (ctx === "not-found") notFound();

  return renderRepoHome(ctx);
}

type Ctx = {
  workspace: ApiWorkspace;
  repo: ApiActivatedRepo;
  repos: ApiActivatedRepo[];
};

async function loadContext(
  token: string,
  slug: string,
): Promise<Ctx | "unauthorized" | "down" | "empty" | "not-found"> {
  let workspaces: ApiWorkspace[];
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
  if (workspaces.length === 0) return "empty";
  const workspace = workspaces[0];
  let repos: ApiActivatedRepo[];
  try {
    repos = await listActivatedRepos(workspace.id, token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
  const repo = findRepoBySlug(repos, slug);
  if (!repo) return "not-found";
  return { workspace, repo, repos };
}

function renderRepoHome(ctx: Ctx) {
  const { workspace, repo, repos } = ctx;
  const base = `/r/${repo.full_name}`;
  return (
    <AppShell
      title={`${repo.full_name}`}
      kicker="repo"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: repo.id,
      }}
      actions={
        <>
          <Link
            href={`${base}/settings`}
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            Repo settings
          </Link>
          <ButtonPrimary>
            <Link href={`${base}/requests`}>Start a request →</Link>
          </ButtonPrimary>
        </>
      }
    >
      <RepoHomeBody repo={repo} base={base} />
    </AppShell>
  );
}

function renderMock(slug: string) {
  const base = `/r/${slug}`;
  const ownerRepo = slug;
  return (
    <AppShell title={ownerRepo} kicker="repo · mock">
      <MockBanner />
      <RepoHomeBody
        repo={
          {
            id: "mock",
            external_id: 0,
            full_name: ownerRepo,
            default_branch: "main",
            private: false,
            html_url: `https://github.com/${ownerRepo}`,
            description: "Mock repo — backend not configured.",
            activated_at: null,
            provider: "github",
            preset: null,
            installed_bundle_version: null,
            current_bundle_version: 0,
          } as ApiActivatedRepo
        }
        base={base}
      />
    </AppShell>
  );
}

function renderDownState(slug: string) {
  return (
    <AppShell title={slug} kicker="repo">
      <Card>
        <CardHeader
          title="Backend unreachable"
          subtitle="The repo view couldn't load live data."
        />
        <p className="text-sm text-white/70">
          Try again in a few seconds. If this keeps happening, check the
          backend service in your hosting console.
        </p>
      </Card>
    </AppShell>
  );
}

function RepoHomeBody({
  repo,
  base,
}: {
  repo: ApiActivatedRepo;
  base: string;
}) {
  const tiles: {
    href: string;
    label: string;
    body: string;
    tone: "ok" | "info" | "warn";
  }[] = [
    {
      href: `${base}/lanes`,
      label: "Lanes",
      body: "Scheduled + event-driven patterns wired from .ship/config.yml.",
      tone: "info",
    },
    {
      href: `${base}/requests`,
      label: "Requests",
      body: "Catalog of one-shot agent patterns dispatched from this repo.",
      tone: "info",
    },
    {
      href: `${base}/clarifications`,
      label: "Clarifications",
      body: "Tracker-projected items waiting on a human decision.",
      tone: "warn",
    },
    {
      href: `${base}/improvements`,
      label: "Improvements",
      body: "Agent-proposed refactors, housekeeping, follow-ups.",
      tone: "info",
    },
    {
      href: `${base}/artifact-feedback`,
      label: "Feedback",
      body: "Complaints + approvals against catalog artifacts for this repo.",
      tone: "info",
    },
    {
      href: `${base}/knowledge`,
      label: "Knowledge",
      body: "Parsed runbooks, wiki pages, slide decks scoped to this repo.",
      tone: "info",
    },
  ];

  return (
    <>
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Card>
          <CardHeader
            title="Repo home"
            subtitle="PR-1 shell — Now/Trends dashboard content lands in PR-4."
          />
          <p className="text-sm leading-relaxed text-white/70">
            You're in <span className="font-semibold text-white">repo mode</span>
            . Everything the sidebar links is scoped to{" "}
            <span className="font-mono text-aqua">{repo.full_name}</span>. Back
            out to the workspace via the arrow above the sidebar; share this URL
            and collaborators land in the same repo context.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <ButtonPrimary>
              <Link href={`${base}/requests`}>Open requests catalog</Link>
            </ButtonPrimary>
            <ButtonGhost>
              <Link href={`${base}/lanes`}>Review lanes</Link>
            </ButtonGhost>
          </div>
        </Card>
        <Card>
          <CardHeader
            title="Repo facts"
            subtitle={repo.private ? "Private · GitHub" : "Public · GitHub"}
          />
          <dl className="space-y-2 text-sm">
            <Field label="Default branch" value={repo.default_branch} />
            <Field
              label="Preset"
              value={repo.preset ?? "adoption-minimum (implicit)"}
            />
            <Field
              label="Bundle"
              value={
                repo.installed_bundle_version == null
                  ? "never seeded"
                  : `v${repo.installed_bundle_version} / v${repo.current_bundle_version}`
              }
            />
            <Field
              label="Activated"
              value={repo.activated_at ? relativeDate(repo.activated_at) : "—"}
            />
          </dl>
          <div className="mt-4">
            <a
              href={repo.html_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs font-semibold text-aqua hover:underline"
            >
              View on GitHub ↗
            </a>
          </div>
        </Card>
      </section>

      <section className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {tiles.map((tile) => (
          <Link
            key={tile.href}
            href={tile.href}
            className="group rounded-xl border border-white/10 bg-white/[0.03] p-4 transition hover:border-white/25 hover:bg-white/[0.06]"
          >
            <div className="mb-2 flex items-center gap-2">
              <Badge tone={tile.tone}>{tile.label}</Badge>
              <span className="ml-auto text-white/30 transition group-hover:text-white">
                →
              </span>
            </div>
            <p className="text-xs leading-relaxed text-white/60">{tile.body}</p>
          </Link>
        ))}
      </section>
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-white/5 pb-1.5 last:border-b-0">
      <dt className="text-[11px] font-semibold uppercase tracking-widest text-white/45">
        {label}
      </dt>
      <dd className="truncate text-right font-mono text-[11px] text-white/80">
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
