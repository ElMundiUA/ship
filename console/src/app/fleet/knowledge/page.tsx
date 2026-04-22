import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Card, CardHeader } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  getKnowledgeCanonical,
  isApiConfigured,
  listActivatedRepos,
  listWorkspaces,
  type ApiActivatedRepo,
  type ApiKnowledgeCanonicalResponse,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { KnowledgeTabs } from "./knowledge-tabs";

/**
 * Workspace knowledge surface (RFC-0008 §I · PR-7A).
 *
 * Replaces the former ``FleetStub`` with two live tabs:
 *
 * - **Search** — vector search over ``bucket_articles`` +
 *   ``kb_chunks`` re-ranked repo-first / workspace-canonical /
 *   other-repo hints. Repo boost is driven by an optional dropdown
 *   of activated repos.
 * - **Canonical** — inventory of workspace-scope buckets with
 *   ``article_count`` + ``override_count``, plus "orphan slugs"
 *   (slug present in ≥2 repo-scope buckets but no workspace copy)
 *   as candidates for PR-7B promotion.
 *
 * The actual search request is posted through ``/api/knowledge/search``
 * from the client island so the session cookie never has to leave
 * the httpOnly jar. Canonical data is fetched server-side here.
 */

export const dynamic = "force-dynamic";

const NEXT_PATH = "/fleet/knowledge";

export default async function FleetKnowledgePage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="Knowledge" kicker="fleet">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to wire workspace knowledge search."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) {
    redirect(`/login?next=${encodeURIComponent(NEXT_PATH)}`);
  }

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${encodeURIComponent(NEXT_PATH)}`);
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");

  const workspace = workspaces[0];

  let canonical: ApiKnowledgeCanonicalResponse;
  let repos: ApiActivatedRepo[] = [];
  try {
    [canonical, repos] = await Promise.all([
      getKnowledgeCanonical(workspace.id, token),
      listActivatedRepos(workspace.id, token).catch(() => []),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${encodeURIComponent(NEXT_PATH)}`);
    }
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title="Knowledge"
      kicker={`${workspace.name} · fleet`}
    >
      <p className="mb-5 max-w-3xl text-xs text-white/55">
        Workspace-wide knowledge surface. Search runs over{" "}
        <span className="font-mono">bucket_articles</span> and{" "}
        <span className="font-mono">kb_chunks</span> with
        re-ranking: matches in the boosted repo first, workspace-canonical
        articles second, and other repos last. The Canonical tab shows
        which slugs live at workspace scope and which are still
        duplicated across repos.
      </p>

      <KnowledgeTabs
        workspaceId={workspace.id}
        canonical={canonical}
        repos={repos}
      />
    </AppShell>
  );
}

function renderUnavailable(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="Knowledge" kicker="fleet">
      <Card>
        <CardHeader
          title="Couldn't load workspace knowledge"
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
