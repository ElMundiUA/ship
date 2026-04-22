import { Badge } from "@/components/ui";
import type { ApiAgentRequest } from "@/lib/api/client";

/**
 * Shared row for recent agent requests. Used on the workspace
 * ``/requests`` page and the repo-scoped
 * ``/r/[owner]/[repo]/requests`` page. Purely presentational — the
 * caller decides which list to feed in (filtered vs unfiltered).
 */
export function RequestRow({ request }: { request: ApiAgentRequest }) {
  const body = (
    <>
      <div className="flex flex-wrap items-center gap-2">
        {request.pattern_id ? (
          <Badge tone="info">{request.pattern_id}</Badge>
        ) : (
          <Badge tone="neutral">{request.agent_slug}</Badge>
        )}
        <Badge tone={statusTone(request.status)} dot>
          {request.status}
        </Badge>
        <span className="font-mono text-[10px] text-white/45">
          {formatRelative(request.created_at)}
        </span>
      </div>
      <p className="mt-1 font-mono text-[11px] text-white/55">
        {request.repo_full_name}
      </p>
      {request.pattern_id && Object.keys(request.inputs ?? {}).length > 0 ? (
        <ul className="mt-1 space-y-0.5 text-[11px] text-white/60">
          {Object.entries(request.inputs).slice(0, 3).map(([k, v]) => (
            <li key={k} className="truncate">
              <span className="font-mono text-white/45">{k}:</span>{" "}
              <span className="font-mono">{v}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 line-clamp-2 text-[12px] text-white/75">
          {request.prompt}
        </p>
      )}
      {request.context_ref ? (
        <p className="mt-1 truncate font-mono text-[10px] text-white/45">
          ctx: {request.context_ref}
        </p>
      ) : null}
      {request.summary && request.status === "dispatch_failed" ? (
        <p className="mt-1 text-[10px] text-coral">{request.summary}</p>
      ) : null}
    </>
  );
  const className =
    "block rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 transition hover:border-white/25 hover:bg-white/[0.04]";

  if (request.gh_html_url) {
    return (
      <li>
        <a
          href={request.gh_html_url}
          target="_blank"
          rel="noreferrer"
          className={className}
        >
          {body}
        </a>
      </li>
    );
  }
  return <li className={className}>{body}</li>;
}

function statusTone(status: string): "ok" | "warn" | "err" | "neutral" | "info" {
  switch (status) {
    case "succeeded":
      return "ok";
    case "failed":
    case "dispatch_failed":
      return "err";
    case "dispatching":
      return "warn";
    case "dispatched":
      return "info";
    default:
      return "neutral";
  }
}

function formatRelative(iso: string): string {
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return iso;
  const sec = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.round(hr / 24);
  return `${days}d ago`;
}
