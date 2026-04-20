import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, Card, CardHeader } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  type ApiChatThread,
  isApiConfigured,
  listActivatedRepos,
  listChatThreads,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * Chat list + new-thread composer (C10).
 *
 * The `/chat` surface is a minimalist two-pane layout: list of
 * threads on the left, composer for a new conversation on the
 * right. Clicking a thread routes to `/chat/[id]` for the full
 * conversation view.
 */

export const dynamic = "force-dynamic";

type BannerKind = { tone: "ok" | "warn" | "err"; text: string };

function pickBanner(param: string | undefined): BannerKind | null {
  if (!param) return null;
  switch (param) {
    case "resolved":
      return { tone: "ok", text: "Thread resolved." };
    case "archived":
      return { tone: "ok", text: "Thread archived." };
    case "empty":
      return { tone: "warn", text: "Thread title + message are required." };
    case "bad_input":
      return { tone: "warn", text: "Invalid input." };
    case "not_found":
      return { tone: "warn", text: "Thread no longer exists." };
    case "api_unavailable":
      return { tone: "err", text: "Backend unreachable — try again in a moment." };
    default:
      if (param.startsWith("http_")) {
        return { tone: "err", text: `Backend returned ${param.slice(5)}.` };
      }
      return { tone: "err", text: "Something went sideways." };
  }
}

export default async function ChatPage({
  searchParams,
}: {
  searchParams: Promise<{ banner?: string }>;
}) {
  const params = await searchParams;

  if (!isApiConfigured()) {
    return (
      <AppShell title="Chat">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to start new chats."
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
  let threads: ApiChatThread[] = [];
  let repos: Awaited<ReturnType<typeof listActivatedRepos>> = [];
  try {
    [threads, repos] = await Promise.all([
      listChatThreads(workspace.id, token),
      listActivatedRepos(workspace.id, token).catch(() => []),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      redirect("/login?next=%2Fchat");
    return renderUnavailable(err);
  }

  const banner = pickBanner(params.banner);

  return (
    <AppShell
      title="Chat"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: repos[0]?.id ?? null,
      }}
      actions={
        <Link
          href="/"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Dashboard
        </Link>
      }
    >
      <p className="mb-4 max-w-2xl text-xs text-white/55">
        Scope a ticket with the Ship agent before it lands in the tracker.
        The assistant is running as a stub right now — real model wires in
        next sprint.
      </p>

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

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr,380px]">
        <ThreadList threads={threads} />
        <NewThreadCard workspaceId={workspace.id} repos={repos} />
      </div>
    </AppShell>
  );
}

function ThreadList({ threads }: { threads: ApiChatThread[] }) {
  if (threads.length === 0) {
    return (
      <Card>
        <CardHeader
          title="No threads yet"
          subtitle="Open a thread with the agent on the right to get started."
        />
      </Card>
    );
  }

  return (
    <ul className="space-y-2">
      {threads.map((t) => (
        <li key={t.id}>
          <Link
            href={`/chat/${encodeURIComponent(t.id)}`}
            className="block rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3 transition hover:border-white/20 hover:bg-white/[0.04]"
          >
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-white/50">
              <StatusBadge status={t.status} />
              {t.workflow_id ? <span>{t.workflow_id}</span> : null}
              <span className="ml-auto">
                {new Date(t.updated_at).toLocaleString()}
              </span>
            </div>
            <h3 className="mt-1 font-semibold text-white">{t.title}</h3>
            <p className="mt-1 text-[11px] text-white/45">
              {t.message_count} message{t.message_count === 1 ? "" : "s"}
              {t.resolved_ticket_ref ? (
                <> · resolved as {t.resolved_ticket_ref}</>
              ) : null}
            </p>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function StatusBadge({ status }: { status: ApiChatThread["status"] }) {
  const styles: Record<ApiChatThread["status"], string> = {
    active: "border-aqua/40 bg-aqua/10 text-aqua",
    resolved: "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
    archived: "border-white/20 bg-white/5 text-white/50",
  };
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${styles[status]}`}
    >
      {status}
    </span>
  );
}

function NewThreadCard({
  workspaceId,
  repos,
}: {
  workspaceId: string;
  repos: { id: string; full_name: string }[];
}) {
  return (
    <Card>
      <CardHeader
        title="Start a new thread"
        subtitle="The agent replies inline. Type *resolve: TICKET-123* in chat (or use the button) to create a ticket."
      />
      <form
        action="/api/chat/create-thread"
        method="POST"
        className="mt-4 space-y-3"
      >
        <input type="hidden" name="ws" value={workspaceId} />
        <label className="block text-[11px] font-semibold uppercase tracking-wider text-white/55">
          Title
          <input
            type="text"
            name="title"
            required
            maxLength={200}
            placeholder="e.g. Retry + idempotency for webhooks"
            className="mt-1 w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white placeholder-white/30 focus:border-aqua focus:outline-none"
          />
        </label>
        <label className="block text-[11px] font-semibold uppercase tracking-wider text-white/55">
          What are you trying to do?
          <textarea
            name="initial_message"
            required
            className="mt-1 min-h-[120px] w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white placeholder-white/30 focus:border-aqua focus:outline-none"
            placeholder="Describe the problem. The agent will ask follow-ups."
          />
        </label>
        <label className="block text-[11px] font-semibold uppercase tracking-wider text-white/55">
          Repo (optional)
          <select
            name="repo_id"
            className="mt-1 w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white focus:border-aqua focus:outline-none"
            defaultValue=""
          >
            <option value="">— none —</option>
            {repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.full_name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          className="w-full rounded-md bg-aqua px-3 py-2 text-sm font-semibold text-black hover:bg-aqua/90"
        >
          Open thread
        </button>
      </form>
    </Card>
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
