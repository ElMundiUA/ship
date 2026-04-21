import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ScopePill } from "@/components/scope-pill";
import { resolveScopeFromSearch } from "@/lib/scope";
import { Card, CardHeader } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  type ApiBucket,
  type ApiChatThread,
  getActiveChatThread,
  getMe,
  isApiConfigured,
  listActivatedRepos,
  listBuckets,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { type BucketScopeFilter, BucketsSidebar } from "./buckets-sidebar";
import { SingleWindowChat } from "./single-window-chat";

/**
 * C12 — Real agent, single window.
 *
 * The old "thread list + composer" surface from C10 has been
 * replaced by exactly one active conversation plus a named-bucket
 * sidebar. Topic shifts are detected server-side and surfaced
 * through the streaming ``topic_shift`` SSE event; the user either
 * confirms (packing the current thread into a bucket) or dismisses
 * and keeps going.
 */

export const dynamic = "force-dynamic";

export default async function ChatPage({
  searchParams,
}: {
  searchParams: Promise<{
    scope?: string;
    repo_id?: string;
    project_id?: string;
  }>;
}) {
  const params = await searchParams;
  const scope = resolveScopeFromSearch(params);
  if (!isApiConfigured()) {
    return (
      <AppShell title="Navigator">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to use Navigator with the agent."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fchat");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      redirect("/login?next=%2Fchat");
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace = workspaces[0];

  let thread: ApiChatThread | null = null;
  let buckets: ApiBucket[] = [];
  let repos: Awaited<ReturnType<typeof listActivatedRepos>> = [];
  let me: Awaited<ReturnType<typeof getMe>> | null = null;
  try {
    [thread, buckets, repos, me] = await Promise.all([
      getActiveChatThread(workspace.id, token),
      listBuckets(workspace.id, { token }).catch(() => []),
      listActivatedRepos(workspace.id, token).catch(() => []),
      getMe(token).catch(() => null),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      redirect("/login?next=%2Fchat");
    if (err instanceof ApiHttpError && err.status === 412) {
      // Backend is up but no LLM key configured — render a crisp
      // error instead of the raw "Precondition Failed" page.
      return (
        <AppShell
          title="Navigator"
          workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
        >
          <Card>
            <CardHeader
              title="Agent not configured"
              subtitle="Set OPENAI_API_KEY (or ANTHROPIC_API_KEY) on the backend to turn the agent on."
            />
          </Card>
        </AppShell>
      );
    }
    return renderUnavailable(err);
  }
  if (!thread) return renderUnavailable(new Error("empty chat response"));

  const scopePill = (
    <ScopePill
      workspaceName={workspace.name}
      repos={repos.map((r) => ({ id: r.id, full_name: r.full_name }))}
      me={
        me
          ? { id: me.id, email: me.email, display_name: me.display_name }
          : null
      }
    />
  );

  // Translate the URL-driven scope into a filter shape the sidebar
  // knows how to apply. Active-thread selection still ignores
  // ``repo_id`` (backend work), so scope today only narrows the
  // sidebar — the conversation surface stays on the same thread.
  const bucketScope: BucketScopeFilter =
    scope.kind === "repo"
      ? { kind: "repo", repoId: scope.repoId }
      : scope.kind === "user"
        ? { kind: "user", userId: me?.id ?? null }
        : { kind: "workspace" };

  return (
    <AppShell
      title="Navigator"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: scope.kind === "repo" ? scope.repoId : repos[0]?.id ?? null,
      }}
      scopePill={scopePill}
      actions={
        <Link
          href="/"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Dashboard
        </Link>
      }
    >
      {scope.kind !== "workspace" ? (
        <div className="mb-4 rounded-lg border border-aqua/20 bg-aqua/5 px-3 py-2 text-[12px] text-aqua/85">
          Scope filter narrows the memory-bucket sidebar. The active
          conversation still uses the one thread routed to you;
          repo-scoped threads are on the roadmap.
        </div>
      ) : null}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr,320px]">
        <SingleWindowChat
          workspaceId={workspace.id}
          thread={{
            id: thread.id,
            title: thread.title,
            status: thread.status,
            topic_summary: thread.topic_summary,
            packed_into_bucket_id: thread.packed_into_bucket_id,
            created_at: thread.created_at,
            updated_at: thread.updated_at,
            messages: thread.messages,
          }}
        />
        <BucketsSidebar
          workspaceId={workspace.id}
          initial={buckets}
          scopeFilter={bucketScope}
        />
      </div>
    </AppShell>
  );
}

function renderUnavailable(err: unknown) {
  const msg =
    err instanceof ApiUnavailableError
      ? err.message
      : err instanceof Error
        ? err.message
        : String(err);
  return (
    <AppShell title="Navigator">
      <Card>
        <CardHeader
          title="Backend unavailable"
          subtitle="The console couldn't reach the Ship API. Retry in a moment."
        />
        <p className="mt-2 font-mono text-[11px] text-rose-300">{msg}</p>
      </Card>
    </AppShell>
  );
}
