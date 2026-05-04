import Link from "next/link";
import { redirect } from "next/navigation";

import { PageBody, PageHeader } from "@/components/app-shell";
import { Card, CardHeader, EmptyState } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  type ApiChatThreadSummary,
  isApiConfigured,
  listChatThreads,
} from "@/lib/api/client";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";

/**
 * Wave C — read-only archive list for chat threads.
 *
 * The single-window UX (``/chat``) deliberately hides the
 * conversation history; this page is the small escape hatch
 * that lets a user scroll back through threads the idle-thread
 * sweeper or "new conversation" button has put aside.
 */

export const dynamic = "force-dynamic";

export default async function ArchivedChatsPage() {
  if (!isApiConfigured()) {
    return (
      <>
        <PageHeader title="Archived chats" />
        <PageBody>
          <Card>
            <CardHeader
              title="Backend not configured"
              subtitle="Set SHIP_API_URL to use Navigator with the agent."
            />
          </Card>
        </PageBody>
      </>
    );
  }

  const token = await getCachedSessionToken();
  if (!token) redirect("/login?next=%2Fchat%2Farchived");

  const workspaces = await getCachedWorkspaces();
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
    <>
      <PageHeader
        title="Archived chats"
        actions={
          <Link
            href="/chat"
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            ← Back to chat
          </Link>
        }
      />
      <PageBody>
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
      </PageBody>
    </>
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
    <>
      <PageHeader title="Archived chats" />
      <PageBody>
        <Card>
          <CardHeader
            title="Backend unavailable"
            subtitle="The console couldn't reach the Ship API. Retry in a moment."
          />
          <p className="mt-2 font-mono text-[11px] text-rose-300">{msg}</p>
        </Card>
      </PageBody>
    </>
  );
}
