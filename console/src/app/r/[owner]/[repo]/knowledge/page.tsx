import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ResolvedBucketGrid } from "@/components/resolved-bucket-grid";
import { ButtonGhost, Card, CardHeader, LiveBanner } from "@/components/ui";
import {
  ApiHttpError,
  isApiConfigured,
  listResolvedBuckets,
} from "@/lib/api/client";
import type { ApiResolvedBucket } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";
import { resolveRepoContext } from "@/lib/repo-context";
import { slugFromParams, type RepoRouteParams } from "@/lib/repo-slug";
import { toAppShellWorkspaces, withWorkspaceQuery } from "@/lib/workspace-scope";

/**
 * Repo-mode Knowledge (``/r/<owner>/<repo>/knowledge``).
 *
 * Only renders the Phase-3 resolver output — no ``.ship/knowledge``
 * legacy mirror grid, no workspace/user scope toggle, no
 * ``NewBucketDialog``. The repo is locked by the URL, so
 * ``listResolvedBuckets`` is called with ``repoId`` and we show the
 * effective winners only (same pattern as the workspace page for
 * ``scope=repo``).
 */

export const dynamic = "force-dynamic";

export default async function RepoKnowledgePage({
  params,
  searchParams,
}: {
  params: Promise<RepoRouteParams>;
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [resolved, rawSearch] = await Promise.all([
    params,
    searchParams ?? Promise.resolve({} as Record<string, string | string[] | undefined>),
  ]);
  const slug = slugFromParams(resolved);
  if (!slug) notFound();
  const here = `/r/${slug}/knowledge`;

  if (!isApiConfigured()) {
    return (
      <AppShell title="Knowledge" kicker={`${slug} · repo`}>
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to load knowledge buckets."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect(`/login?next=${encodeURIComponent(here)}`);

  const result = await resolveRepoContext(token, slug, rawSearch);
  if (result.kind === "unauthorized") {
    redirect(`/login?next=${encodeURIComponent(here)}`);
  }
  if (result.kind === "down") return renderDownState(slug);
  if (result.kind === "empty") redirect("/onboarding?step=github");
  if (result.kind === "not-found") notFound();

  const ctx = result.ctx;
  const multi = ctx.allWorkspaces.length > 1;

  let buckets: ApiResolvedBucket[] = [];
  try {
    const resp = await listResolvedBuckets(
      ctx.workspace.id,
      { repoId: ctx.repo.id },
      token,
    );
    buckets = resp.buckets.filter((b) => b.effective);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${encodeURIComponent(here)}`);
    }
    return renderDownState(slug);
  }

  return (
    <AppShell
      kicker={`${ctx.repo.full_name} · knowledge`}
      title="Knowledge buckets"
      workspace={{
        id: ctx.workspace.id,
        name: ctx.workspace.name,
        slug: ctx.workspace.slug,
      }}
      allWorkspaces={toAppShellWorkspaces(ctx.allWorkspaces)}
      scope={{
        repos: ctx.repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: ctx.repo.id,
      }}
      actions={
        <Link
          href={withWorkspaceQuery(
            `/r/${ctx.repo.full_name}`,
            ctx.workspace.id,
            multi,
          )}
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Repo
        </Link>
      }
    >
      <LiveBanner workspace={ctx.workspace.slug} />

      <p className="mb-5 max-w-3xl text-sm text-white/65">
        Buckets visible to{" "}
        <span className="font-mono text-white/80">{ctx.repo.full_name}</span> —
        per-repo overlays plus the workspace-wide canonicals that
        this repo inherits. Use the workspace{" "}
        <Link
          href={withWorkspaceQuery("/knowledge", ctx.workspace.id, multi)}
          className="text-aqua hover:underline"
        >
          knowledge surface
        </Link>{" "}
        to edit canonicals or pack new buckets from Navigator.
      </p>

      <div className="mb-6 flex justify-end">
        <ButtonGhost>
          <Link
            href={withWorkspaceQuery("/knowledge", ctx.workspace.id, multi)}
          >
            Browse all buckets →
          </Link>
        </ButtonGhost>
      </div>

      <ResolvedBucketGrid buckets={buckets} scopeKind="repo" />
    </AppShell>
  );
}

function renderDownState(slug: string) {
  return (
    <AppShell title="Knowledge" kicker={`${slug} · repo`}>
      <Card>
        <CardHeader
          title="Backend unreachable"
          subtitle="The knowledge list couldn't load — try again in a moment."
        />
      </Card>
    </AppShell>
  );
}
