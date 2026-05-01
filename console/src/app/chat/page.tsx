import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ScopePill } from "@/components/scope-pill";
import { resolveScopeFromSearch } from "@/lib/scope";
import { Card, CardHeader } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  type ApiChatThread,
  getActiveChatThread,
  getMe,
  isApiConfigured,
  listActivatedRepos,
  listChatThreads,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import {
  pickWorkspace,
  toAppShellWorkspaces,
  withWorkspaceQuery,
} from "@/lib/workspace-scope";

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
    ws?: string;
    from_inbox?: string;
  }>;
}) {
  const params = (await searchParams) as Record<
    string,
    string | string[] | undefined
  >;
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
  const resolved = await getResolvedWorkspaceId(params, workspaces);
  const workspace = pickWorkspace(workspaces, resolved);
  const multi = workspaces.length > 1;

  let thread: ApiChatThread | null = null;
  let repos: Awaited<ReturnType<typeof listActivatedRepos>> = [];
  let me: Awaited<ReturnType<typeof getMe>> | null = null;
  try {
    [thread, repos, me] = await Promise.all([
      getActiveChatThread(workspace.id, token),
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
          allWorkspaces={toAppShellWorkspaces(workspaces)}
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

  // Cheap probe — Wave E3 uses this to decide whether the empty
  // chat surface should advertise the archived-thread link. We
  // ask for a single row and treat any failure as "no archived
  // chats" so the link silently disappears instead of bricking
  // the page.
  let hasArchivedChats = false;
  try {
    const archivedSample = await listChatThreads(
      workspace.id,
      { status: "archived", limit: 1 },
      token,
    );
    hasArchivedChats = archivedSample.length > 0;
  } catch {
    hasArchivedChats = false;
  }

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

  return (
    <AppShell
      title="Navigator"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      allWorkspaces={toAppShellWorkspaces(workspaces)}
      me={
        me
          ? {
              name: me.display_name?.trim() || me.email,
              email: me.email,
              initials: initialsFromMe(me.display_name, me.email),
            }
          : null
      }
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: scope.kind === "repo" ? scope.repoId : repos[0]?.id ?? null,
      }}
      scopePill={scopePill}
      actions={
        <div className="flex items-center gap-4">
          <Link
            href={withWorkspaceQuery("/chat/archived", workspace.id, multi)}
            className="text-xs font-semibold text-white/55 hover:text-white"
          >
            Archived chats
          </Link>
          <Link
            href={withWorkspaceQuery("/", workspace.id, multi)}
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            ← Dashboard
          </Link>
        </div>
      }
    >
      <div className="mx-auto w-full max-w-3xl">
        {typeof params.from_inbox === "string" && params.from_inbox && (
          <div className="mb-4 flex items-center justify-between gap-3 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
            <div>
              <span className="font-semibold text-aqua">📩 Linked inbox item</span>
              <span className="ml-2 text-white/65">
                Navigator was seeded with the ticket + the agent&apos;s
                question. Once you&apos;ve agreed on an answer, head back
                to the inbox row and use “Answer” to record it.
              </span>
            </div>
            <Link
              href={`/inbox/${encodeURIComponent(String(params.from_inbox))}`}
              className="shrink-0 rounded-lg border border-aqua/40 px-3 py-1.5 font-semibold text-aqua hover:bg-aqua/10"
            >
              Open inbox row →
            </Link>
          </div>
        )}
        <SingleWindowChat
          workspaceId={workspace.id}
          hasArchivedChats={hasArchivedChats}
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
      </div>
    </AppShell>
  );
}

function initialsFromMe(displayName: string | null, email: string): string {
  const source = displayName?.trim() || email;
  const parts = source.split(/[\s@.]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return (parts[0]?.slice(0, 2) ?? "??").toUpperCase();
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
