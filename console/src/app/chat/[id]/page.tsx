import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, Card, CardHeader } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  type ApiChatMessage,
  type ApiChatThreadDetail,
  getChatThread,
  isApiConfigured,
  listActivatedRepos,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * Chat thread detail (C10).
 *
 * Renders the full transcript for one thread, with a composer at
 * the bottom (active threads only) and a "resolve / archive" strip.
 * The page is server-rendered on every request so a form POST ->
 * redirect cycle is enough to keep the transcript fresh (no
 * websocket, no polling).
 */

export const dynamic = "force-dynamic";

type BannerKind = { tone: "ok" | "warn" | "err"; text: string };

function pickBanner(param: string | undefined): BannerKind | null {
  if (!param) return null;
  switch (param) {
    case "empty":
      return { tone: "warn", text: "Message body is required." };
    case "closed":
      return {
        tone: "warn",
        text: "Thread is closed — can't append more messages.",
      };
    case "already_closed":
      return { tone: "warn", text: "Thread is already closed." };
    case "ticket_required":
      return {
        tone: "warn",
        text: "A ticket ref is required when resolving.",
      };
    case "api_unavailable":
      return { tone: "err", text: "Backend unreachable — try again in a moment." };
    default:
      if (param.startsWith("http_")) {
        return { tone: "err", text: `Backend returned ${param.slice(5)}.` };
      }
      return { tone: "err", text: "Something went sideways." };
  }
}

export default async function ChatThreadPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ banner?: string }>;
}) {
  const { id } = await params;
  const sp = await searchParams;

  if (!isApiConfigured()) {
    return (
      <AppShell title="Chat">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to open chat threads."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect(`/login?next=%2Fchat%2F${encodeURIComponent(id)}`);

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      redirect(`/login?next=%2Fchat%2F${encodeURIComponent(id)}`);
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");

  const workspace = workspaces[0];

  let thread: ApiChatThreadDetail;
  let repos: Awaited<ReturnType<typeof listActivatedRepos>> = [];
  try {
    [thread, repos] = await Promise.all([
      getChatThread(workspace.id, id, token),
      listActivatedRepos(workspace.id, token).catch(() => []),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        redirect(`/login?next=%2Fchat%2F${encodeURIComponent(id)}`);
      if (err.status === 404) notFound();
    }
    return renderUnavailable(err);
  }

  const repoName =
    thread.repo_id && repos.find((r) => r.id === thread.repo_id)?.full_name;
  const banner = pickBanner(sp.banner);

  return (
    <AppShell
      title={thread.title}
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: thread.repo_id ?? repos[0]?.id ?? null,
      }}
      actions={
        <Link
          href="/chat"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← All threads
        </Link>
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] text-white/50">
        <Badge>{thread.status}</Badge>
        {repoName ? <span>{repoName}</span> : null}
        {thread.workflow_id ? <span>workflow: {thread.workflow_id}</span> : null}
        {thread.resolved_ticket_ref ? (
          <span>ticket: {thread.resolved_ticket_ref}</span>
        ) : null}
        <span className="ml-auto">
          updated {new Date(thread.updated_at).toLocaleString()}
        </span>
      </div>

      {banner ? (
        <div
          className={`mb-4 rounded-lg border px-3 py-2 text-[12px] ${
            banner.tone === "ok"
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
              : banner.tone === "warn"
                ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
                : "border-rose-500/30 bg-rose-500/10 text-rose-200"
          }`}
        >
          {banner.text}
        </div>
      ) : null}

      <Transcript messages={thread.messages} />

      {thread.status === "active" ? (
        <Composer workspaceId={workspace.id} thread={thread} />
      ) : (
        <div className="mt-6 rounded-lg border border-white/10 bg-white/[0.02] px-4 py-3 text-[12px] text-white/55">
          This thread is {thread.status}. Open a new thread to continue.
        </div>
      )}
    </AppShell>
  );
}

function Transcript({ messages }: { messages: ApiChatMessage[] }) {
  return (
    <ol className="space-y-3">
      {messages.map((m) => (
        <li
          key={m.id}
          className={`max-w-2xl rounded-2xl border px-4 py-3 text-sm ${
            m.role === "user"
              ? "ml-auto border-aqua/30 bg-aqua/10 text-white"
              : m.role === "assistant"
                ? "border-white/10 bg-white/[0.03] text-white/85"
                : "border-amber-500/30 bg-amber-500/5 text-amber-100"
          }`}
        >
          <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wider opacity-70">
            <span>{m.role}</span>
            <span>{new Date(m.created_at).toLocaleTimeString()}</span>
          </div>
          <div className="whitespace-pre-wrap">{m.body}</div>
          {m.meta && (m.meta as { stub?: boolean }).stub ? (
            <div className="mt-2 text-[10px] text-white/35">
              stub reply — real model wires in next sprint
            </div>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function Composer({
  workspaceId,
  thread,
}: {
  workspaceId: string;
  thread: ApiChatThreadDetail;
}) {
  return (
    <div className="mt-6 space-y-4">
      <form
        action="/api/chat/append"
        method="POST"
        className="rounded-xl border border-white/10 bg-white/[0.02] p-3"
      >
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="thread_id" value={thread.id} />
        <textarea
          name="body"
          required
          className="min-h-[80px] w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white placeholder-white/30 focus:border-aqua focus:outline-none"
          placeholder="Type a message…"
        />
        <div className="mt-2 flex items-center justify-end gap-2">
          <button
            type="submit"
            className="rounded-md bg-aqua px-3 py-1.5 text-xs font-semibold text-black hover:bg-aqua/90"
          >
            Send
          </button>
        </div>
      </form>

      <form
        action="/api/chat/resolve"
        method="POST"
        className="flex flex-wrap items-center gap-2 rounded-xl border border-white/10 bg-white/[0.02] p-3"
      >
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="thread_id" value={thread.id} />
        <input
          type="text"
          name="ticket_ref"
          placeholder="TICKET-123"
          className="w-40 rounded-md border border-white/10 bg-black/30 px-2 py-1.5 text-xs text-white placeholder-white/30 focus:border-emerald-400 focus:outline-none"
        />
        <label className="flex items-center gap-1 text-[11px] text-white/55">
          <input type="checkbox" name="create_improvement" defaultChecked />
          Also log to Improvements
        </label>
        <button
          type="submit"
          name="action"
          value="resolved"
          className="rounded-md bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-black hover:bg-emerald-400"
        >
          Resolve with ticket
        </button>
        <button
          type="submit"
          name="action"
          value="archived"
          className="ml-auto rounded-md border border-white/10 px-3 py-1.5 text-xs font-semibold text-white/65 hover:bg-white/5"
        >
          Archive (no ticket)
        </button>
      </form>
    </div>
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
    <AppShell title="Chat">
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
