import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Card, CardHeader, EmptyState } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  type ApiChatThreadSummary,
  isApiConfigured,
  listChatThreads,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * Wave C — read-only archive list for chat threads.
 *
 * The single-window UX (``/chat``) deliberately hides the
 * conversation history; this page is the small escape hatch
 * that lets a user scroll back through threads the idle-thread
 * sweeper or "new conversation" button has put aside.
 *
 * Light touch on purpose:
 *
 * - No filters / search — pagination is handled by the API's
 *   ``limit=`` cap; we ask for 50 rows, oldest scrolls off the
 *   bottom. Real pagination can land if usage ever asks for it.
 * - No transcript preview — clicking a row would open the full
 *   thread, but the single-window detail surface is not in this
 *   wave. We render archived metadata only and link a row's
 *   title back to ``/chat`` so the user has a clear way back.
 */

export const dynamic = "force-dynamic";

export default async function ArchivedChatsPage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="Archived chats">
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
  if (!token) redirect("/login?next=%2Fchat%2Farchived");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      redirect("/login?next=%2Fchat%2Farchived");
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace = workspaces[0];

  let threads: ApiChatThreadSummary[] = [];
  try {
    threads = await listChatThreads(
      workspace.id,
      { status: "archived", limit: 50 },
      token,
    );
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      redirect("/login?next=%2Fchat%2Farchived");
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title="Archived chats"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      actions={
        <Link
          href="/chat"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Back to chat
        </Link>
      }
    >
      <div className="mx-auto w-full max-w-3xl">
        <Card>
          <CardHeader
            title="Archived chats"
            subtitle="Conversations the idle sweeper or “new chat” button put aside."
          />
          {threads.length === 0 ? (
            <EmptyState
              title="No archived chats yet."
              body="Conversations move here automatically after seven days of silence, or when you start a fresh thread."
            />
          ) : (
            <ul className="divide-y divide-white/5">
              {threads.map((t) => (
                <li key={t.id} className="py-3">
                  <ArchivedRow thread={t} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </AppShell>
  );
}

function ArchivedRow({ thread }: { thread: ApiChatThreadSummary }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <Link
          href="/chat"
          className="block truncate font-display text-sm font-semibold text-white hover:text-aqua"
        >
          {thread.title || "Untitled conversation"}
        </Link>
        {thread.topic_summary && (
          <p className="mt-0.5 line-clamp-2 text-xs text-white/55">
            {thread.topic_summary}
          </p>
        )}
      </div>
      <div className="shrink-0 text-right text-[10px] uppercase tracking-[0.18em] text-white/45">
        {formatArchivedAt(thread)}
      </div>
    </div>
  );
}

function formatArchivedAt(thread: ApiChatThreadSummary): string {
  const ts = thread.archived_at ?? thread.updated_at;
  try {
    return new Date(ts).toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return ts;
  }
}

function renderUnavailable(err: unknown) {
  const msg =
    err instanceof ApiUnavailableError
      ? err.message
      : err instanceof Error
        ? err.message
        : String(err);
  return (
    <AppShell title="Archived chats">
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
