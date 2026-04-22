import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { RequestsCatalog } from "@/app/requests/requests-catalog";
import { RequestRow } from "@/components/request-row";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiAgentRequest,
  type ApiCatalogPattern,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listAgentRequests,
  listCatalogPatterns,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { resolveRepoContext } from "@/lib/repo-context";
import { slugFromParams, type RepoRouteParams } from "@/lib/repo-slug";

/**
 * Repo-mode Requests (``/r/<owner>/<repo>/requests``).
 *
 * Reuses :component:`RequestsCatalog` with ``lockedRepoId`` so the
 * repo selector disappears and every dispatch fires against this
 * repo. The "Recent requests" rail calls ``listAgentRequests`` with
 * ``repoId`` so the list is already scoped without a client-side
 * filter.
 */

export const dynamic = "force-dynamic";

export default async function RepoRequestsPage({
  params,
}: {
  params: Promise<RepoRouteParams>;
}) {
  const resolved = await params;
  const slug = slugFromParams(resolved);
  if (!slug) notFound();
  const here = `/r/${slug}/requests`;

  if (!isApiConfigured()) {
    return (
      <AppShell title="Requests" kicker={`${slug} · repo`}>
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to wire one-shot agent runs."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect(`/login?next=${encodeURIComponent(here)}`);

  const result = await resolveRepoContext(token, slug);
  if (result.kind === "unauthorized") {
    redirect(`/login?next=${encodeURIComponent(here)}`);
  }
  if (result.kind === "down") return renderUnavailable();
  if (result.kind === "empty") redirect("/onboarding?step=github");
  if (result.kind === "not-found") notFound();

  const ctx = result.ctx;

  let requests: ApiAgentRequest[] = [];
  let patterns: ApiCatalogPattern[] = [];
  try {
    [requests, patterns] = await Promise.all([
      listAgentRequests(ctx.workspace.id, {
        token,
        repoId: ctx.repo.id,
        limit: 25,
      }).catch(() => [] as ApiAgentRequest[]),
      listCatalogPatterns({ mode: "request", token }).catch(
        () => [] as ApiCatalogPattern[],
      ),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${encodeURIComponent(here)}`);
    }
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title="Requests"
      kicker={`${ctx.repo.full_name} · repo`}
      workspace={{
        id: ctx.workspace.id,
        name: ctx.workspace.name,
        slug: ctx.workspace.slug,
      }}
      scope={{
        repos: ctx.repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: ctx.repo.id,
      }}
      actions={
        <Link
          href={`/r/${ctx.repo.full_name}/lanes`}
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Lanes
        </Link>
      }
    >
      <p className="mb-5 max-w-3xl text-xs text-white/55">
        Pick a ready-made agent (BA, QA, architect review…), fill in
        the pattern&rsquo;s inputs, dispatch. Unlike{" "}
        <Link
          href={`/r/${ctx.repo.full_name}/lanes`}
          className="text-aqua hover:underline"
        >
          lanes
        </Link>
        , requests don&apos;t land in{" "}
        <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
          .ship/config.yml
        </code>{" "}
        — they&apos;re one-shot dispatches against{" "}
        <span className="font-mono text-white/75">{ctx.repo.full_name}</span>
        &rsquo;s default branch.
      </p>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <RequestsCatalog
            workspaceId={ctx.workspace.id}
            repos={[ctx.repo]}
            patterns={patterns}
            lockedRepoId={ctx.repo.id}
          />
        </div>

        <Card className="lg:col-span-2">
          <CardHeader
            title="Recent requests"
            subtitle="Newest first for this repo. Click any row for the GitHub Actions run."
          />
          {requests.length === 0 ? (
            <div className="mt-5 rounded-lg border border-dashed border-white/10 bg-white/[0.02] p-6 text-center text-xs text-white/55">
              <p>Nothing dispatched yet for this repo.</p>
              <p className="mt-1 text-[11px] text-white/40">
                Pick a pattern on the left — it&apos;ll show up here
                with a link to the Actions run.
              </p>
            </div>
          ) : (
            <ul className="mt-4 space-y-2">
              {requests.map((r) => (
                <RequestRow key={r.id} request={r} />
              ))}
            </ul>
          )}
        </Card>
      </div>
    </AppShell>
  );
}

function renderUnavailable(err?: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="Requests">
      <Card>
        <CardHeader
          title="Couldn't load the Requests surface"
          subtitle={
            isUnavailable
              ? "Backend is unreachable. Try again in a few seconds."
              : "Something went wrong."
          }
        />
      </Card>
    </AppShell>
  );
}
