import { Badge } from "@/components/ui";
import type {
  ApiClarification,
  ApiClarificationStatus,
} from "@/lib/api/client";

/**
 * Shared clarification row. Used by both:
 *
 * - ``console/src/app/clarifications/page.tsx`` — workspace-scoped
 *   inbox with its ``ScopePill`` / ``?scope=`` query ceremony.
 * - ``console/src/app/r/[owner]/[repo]/clarifications/page.tsx`` —
 *   repo-scoped inbox where scope is always "this repo".
 *
 * Kept pure-display and decision-form: no data fetching, no URL
 * building. The ``status_filter`` hidden field lets the POST handler
 * know which tab to redirect back to so the user stays on the tab
 * they were looking at.
 */
export function ClarificationRow({
  row,
  workspaceId,
  repoName,
  statusFilter,
  focused,
}: {
  row: ApiClarification;
  workspaceId: string;
  repoName: string | null;
  statusFilter: ApiClarificationStatus;
  focused: boolean;
}) {
  const context = row.context || {};
  const contextKeys = Object.keys(context);
  const created = new Date(row.created_at);

  return (
    <li
      className={`rounded-xl border px-4 py-4 transition ${
        focused
          ? "border-aqua/40 bg-aqua/5"
          : "border-white/10 bg-white/[0.02]"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-white/45">
            {row.ticket_ref ? <Badge>{row.ticket_ref}</Badge> : null}
            {repoName ? <span>{repoName}</span> : null}
            <span>{created.toLocaleString()}</span>
            {row.pipeline_run_id ? (
              <span>
                from run{" "}
                <span className="font-mono text-white/55">
                  {row.pipeline_run_id.slice(0, 8)}
                </span>
              </span>
            ) : null}
          </div>
          <p className="mt-2 text-sm text-white/85">{row.question}</p>
          {contextKeys.length > 0 ? (
            <details className="mt-2">
              <summary className="cursor-pointer text-[11px] text-white/45 hover:text-white/70">
                context ({contextKeys.length})
              </summary>
              <pre className="mt-2 overflow-x-auto rounded bg-black/30 p-2 text-[11px] text-white/70">
                {JSON.stringify(context, null, 2)}
              </pre>
            </details>
          ) : null}
        </div>
      </div>

      {row.status === "answered" ? (
        <div className="mt-3 rounded-lg bg-emerald-500/5 p-3 text-[13px] text-emerald-100">
          <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wider text-emerald-200/70">
            <span>
              Answered{" "}
              {row.answered_by_email ? <>by {row.answered_by_email}</> : null}
            </span>
            <span>
              {row.answered_at
                ? new Date(row.answered_at).toLocaleString()
                : ""}
            </span>
          </div>
          <div className="whitespace-pre-wrap">{row.answer}</div>
        </div>
      ) : row.status === "skipped" ? (
        <div className="mt-3 text-[12px] text-white/50">
          Skipped{" "}
          {row.answered_by_email ? <>by {row.answered_by_email}</> : null}
          {row.answered_at
            ? ` · ${new Date(row.answered_at).toLocaleString()}`
            : ""}
        </div>
      ) : null}

      {statusFilter === "open" ? (
        <form
          action="/api/clarifications/answer"
          method="POST"
          className="mt-3 flex flex-col gap-2"
        >
          <input type="hidden" name="ws" value={workspaceId} />
          <input type="hidden" name="id" value={row.id} />
          <input type="hidden" name="status_filter" value={statusFilter} />
          <textarea
            name="answer"
            placeholder="Answer the agent's question…"
            className="min-h-[72px] w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white placeholder-white/30 focus:border-aqua focus:outline-none"
            autoFocus={focused}
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              name="action"
              value="answer"
              className="rounded-md bg-aqua px-3 py-1.5 text-xs font-semibold text-black hover:bg-aqua/90"
            >
              Send answer
            </button>
            <button
              type="submit"
              name="action"
              value="skip"
              className="rounded-md border border-white/10 px-3 py-1.5 text-xs font-semibold text-white/70 hover:bg-white/5"
            >
              Not relevant
            </button>
          </div>
        </form>
      ) : (
        <form
          action="/api/clarifications/answer"
          method="POST"
          className="mt-3"
        >
          <input type="hidden" name="ws" value={workspaceId} />
          <input type="hidden" name="id" value={row.id} />
          <input type="hidden" name="status_filter" value={statusFilter} />
          <button
            type="submit"
            name="action"
            value="reopen"
            className="text-[11px] font-semibold text-white/55 hover:text-white"
          >
            Reopen
          </button>
        </form>
      )}
    </li>
  );
}
