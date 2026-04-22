import { Badge } from "@/components/ui";
import type {
  ApiImprovement,
  ApiImprovementDecision,
} from "@/lib/api/client";

/**
 * Shared improvement row + decision forms. Used by:
 *
 * - ``console/src/app/improvements/page.tsx`` — workspace-scoped
 *   backlog with its ``ScopePill`` / ``?scope=`` query ceremony.
 * - ``console/src/app/r/[owner]/[repo]/improvements/page.tsx`` —
 *   repo-scoped backlog where scope is always "this repo".
 *
 * The POST endpoints carry a ``decision_filter`` hidden field so the
 * server action redirects back to the tab the user was browsing.
 */
export function ImprovementRow({
  row,
  workspaceId,
  repoName,
  decisionFilter,
  focused,
}: {
  row: ApiImprovement;
  workspaceId: string;
  repoName: string | null;
  decisionFilter: ApiImprovementDecision;
  focused: boolean;
}) {
  const created = new Date(row.created_at);
  const contextKeys = Object.keys(row.context || {});
  return (
    <li
      id={`imp-${row.id}`}
      className={`rounded-xl border px-4 py-4 transition ${
        focused
          ? "border-aqua/40 bg-aqua/5"
          : "border-white/10 bg-white/[0.02]"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-white/45">
            <Badge>{row.kind}</Badge>
            {repoName ? <span>{repoName}</span> : null}
            <span>{created.toLocaleString()}</span>
            {row.impact ? (
              <span className="text-white/55">impact: {row.impact}</span>
            ) : null}
            {row.effort ? (
              <span className="text-white/55">effort: {row.effort}</span>
            ) : null}
            {row.pipeline_run_id ? (
              <span>
                from run{" "}
                <span className="font-mono text-white/55">
                  {row.pipeline_run_id.slice(0, 8)}
                </span>
              </span>
            ) : null}
          </div>
          <h3 className="mt-1 font-semibold text-white">{row.title}</h3>
          <p className="mt-1 whitespace-pre-wrap text-sm text-white/80">
            {row.body}
          </p>
          {contextKeys.length > 0 ? (
            <details className="mt-2">
              <summary className="cursor-pointer text-[11px] text-white/45 hover:text-white/70">
                context ({contextKeys.length})
              </summary>
              <pre className="mt-2 overflow-x-auto rounded bg-black/30 p-2 text-[11px] text-white/70">
                {JSON.stringify(row.context, null, 2)}
              </pre>
            </details>
          ) : null}
        </div>
      </div>

      {row.decision !== "pending" ? (
        <div
          className={`mt-3 rounded-lg p-3 text-[12px] ${
            row.decision === "accepted"
              ? "bg-emerald-500/5 text-emerald-100"
              : row.decision === "declined"
                ? "bg-rose-500/5 text-rose-100"
                : "bg-amber-500/5 text-amber-100"
          }`}
        >
          <div className="mb-1 text-[10px] uppercase tracking-wider opacity-70">
            {row.decision}
            {row.decided_by_email ? <> · {row.decided_by_email}</> : null}
            {row.decided_at
              ? ` · ${new Date(row.decided_at).toLocaleString()}`
              : ""}
          </div>
          {row.decision_reason ? (
            <div className="whitespace-pre-wrap">{row.decision_reason}</div>
          ) : null}
          {row.next_action_url ? (
            <a
              href={row.next_action_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-block text-[11px] font-semibold text-white/70 underline hover:text-white"
            >
              Open follow-up →
            </a>
          ) : null}
        </div>
      ) : null}

      {decisionFilter === "pending" ? (
        <DecisionForms workspaceId={workspaceId} row={row} />
      ) : (
        <form action="/api/improvements/decide" method="POST" className="mt-3">
          <input type="hidden" name="ws" value={workspaceId} />
          <input type="hidden" name="id" value={row.id} />
          <input type="hidden" name="decision" value="pending" />
          <input type="hidden" name="decision_filter" value={decisionFilter} />
          <button
            type="submit"
            className="text-[11px] font-semibold text-white/55 hover:text-white"
          >
            Undo decision
          </button>
        </form>
      )}
    </li>
  );
}

function DecisionForms({
  workspaceId,
  row,
}: {
  workspaceId: string;
  row: ApiImprovement;
}) {
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      <form action="/api/improvements/decide" method="POST" className="contents">
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="id" value={row.id} />
        <input type="hidden" name="decision_filter" value="pending" />
        <button
          type="submit"
          name="decision"
          value="accepted"
          className="rounded-md bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-black hover:bg-emerald-400"
        >
          Accept
        </button>
        <button
          type="submit"
          name="decision"
          value="deferred"
          className="rounded-md border border-white/10 px-3 py-1.5 text-xs font-semibold text-white/70 hover:bg-white/5"
        >
          Later
        </button>
      </form>
      <form
        action="/api/improvements/decide"
        method="POST"
        className="flex flex-wrap items-center gap-2"
      >
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="id" value={row.id} />
        <input type="hidden" name="decision_filter" value="pending" />
        <input
          type="text"
          name="reason"
          placeholder="why decline?"
          required
          className="rounded-md border border-white/10 bg-black/30 px-2 py-1.5 text-xs text-white placeholder-white/30 focus:border-rose-400 focus:outline-none"
        />
        <button
          type="submit"
          name="decision"
          value="declined"
          className="rounded-md border border-rose-500/30 px-3 py-1.5 text-xs font-semibold text-rose-200 hover:bg-rose-500/10"
        >
          Decline
        </button>
      </form>
    </div>
  );
}
