import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ButtonGhost, ButtonPrimary, Card, CardHeader } from "@/components/ui";
import { isApiConfigured } from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { resolveRepoContext, type RepoContext } from "@/lib/repo-context";
import { slugFromParams, type RepoRouteParams } from "@/lib/repo-slug";

/**
 * Repo-mode Artifact feedback (``/r/<owner>/<repo>/artifact-feedback``).
 *
 * Deliberately a stub. The ``ArtifactFeedback`` model is
 * workspace-scoped today (no ``repo_id`` column, no repo filter on
 * ``GET /artifact-feedback``). Retrofitting that model is a product
 * call — do we scope feedback to the repo, the catalog artifact, or
 * both? — and the team chose to defer it rather than block PR-1 on
 * that decision (see ``documentation/internal/console-refactor-backlog.md``
 * PR-6 Deferred).
 *
 * This page exists so the repo sidebar's "Feedback" link doesn't
 * 404. It explains the current state and bounces the user to the
 * workspace-level surface where all feedback for the tenant lives.
 */

export const dynamic = "force-dynamic";

export default async function RepoArtifactFeedbackPage({
  params,
}: {
  params: Promise<RepoRouteParams>;
}) {
  const resolved = await params;
  const slug = slugFromParams(resolved);
  if (!slug) notFound();
  const basePath = `/r/${slug}/artifact-feedback`;

  if (!isApiConfigured()) {
    return renderStub({ slug, repoFullName: slug, base: `/r/${slug}` });
  }

  const token = await getSessionToken();
  if (!token) redirect(`/login?next=${encodeURIComponent(basePath)}`);

  const result = await resolveRepoContext(token, slug);
  if (result.kind === "unauthorized") {
    redirect(`/login?next=${encodeURIComponent(basePath)}`);
  }
  if (result.kind === "down") {
    return renderStub({ slug, repoFullName: slug, base: `/r/${slug}` });
  }
  if (result.kind === "empty") redirect("/onboarding?step=github");
  if (result.kind === "not-found") notFound();

  return renderWithCtx(result.ctx);
}

function renderWithCtx(ctx: RepoContext) {
  const { workspace, repo, repos } = ctx;
  const base = `/r/${repo.full_name}`;
  return (
    <AppShell
      title="Artifact feedback"
      kicker={`${repo.full_name} · repo`}
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: repo.id,
      }}
      actions={
        <Link
          href={base}
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Repo home
        </Link>
      }
    >
      <StubCard repoFullName={repo.full_name} base={base} />
    </AppShell>
  );
}

function renderStub({
  slug,
  repoFullName,
  base,
}: {
  slug: string;
  repoFullName: string;
  base: string;
}) {
  return (
    <AppShell title="Artifact feedback" kicker={`${slug} · repo`}>
      <StubCard repoFullName={repoFullName} base={base} />
    </AppShell>
  );
}

function StubCard({
  repoFullName,
  base,
}: {
  repoFullName: string;
  base: string;
}) {
  return (
    <Card>
      <CardHeader
        title="Per-repo feedback view is coming"
        subtitle={`Feedback collected for ${repoFullName} currently rolls up to the workspace feed.`}
      />
      <p className="mb-4 max-w-2xl text-sm text-white/70">
        Artifact feedback is still scoped at the workspace tier — every
        complaint or approval against a catalog artifact flows into the
        shared queue. A per-repo filter is on the roadmap; for now,
        use the workspace view and filter by artifact slug.
      </p>
      <div className="flex flex-wrap gap-2">
        <ButtonPrimary>
          <Link href="/artifact-feedback">Open workspace feedback →</Link>
        </ButtonPrimary>
        <ButtonGhost>
          <Link href={base}>Back to repo home</Link>
        </ButtonGhost>
      </div>
    </Card>
  );
}
