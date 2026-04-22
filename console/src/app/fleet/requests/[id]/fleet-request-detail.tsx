"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { Badge, ButtonGhost, Card, CardHeader } from "@/components/ui";
import type {
  ApiAgentRequest,
  ApiFleetRequestCreateOut,
  ApiFleetRequestRejection,
} from "@/lib/api/client";

/**
 * Client-side detail for one fleet request.
 *
 * Owns the cancel action + a refresh shortcut. The repo × status
 * pivot is rendered from server-fetched data; live updates ride on
 * the refresh button until we wire SSE. Rejections surface below
 * the pivot so operators can see exactly what to fix before
 * re-running (GitHub App missing, repo not found, dispatch failed,
 * …).
 */

type State =
  | { mode: "idle" }
  | { mode: "cancelling" }
  | { mode: "error"; message: string };

export function FleetRequestDetail({
  workspaceId,
  detail,
}: {
  workspaceId: string;
  detail: ApiFleetRequestCreateOut;
}) {
  const router = useRouter();
  const [state, setState] = useState<State>({ mode: "idle" });

  const { fleet_request: parent, children, rejections } = detail;
  const cancelable =
    parent.status !== "cancel_requested" &&
    parent.status !== "cancelled" &&
    parent.status !== "failed";

  async function handleCancel() {
    if (!cancelable) return;
    if (!confirm("Cancel this fleet request? Running children will be flagged.")) {
      return;
    }
    setState({ mode: "cancelling" });
    try {
      const res = await fetch(
        `/api/fleet/requests/${encodeURIComponent(parent.id)}/cancel`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ workspaceId }),
        },
      );
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as {
          error?: string;
        };
        setState({
          mode: "error",
          message: data.error || `HTTP ${res.status}`,
        });
        return;
      }
      router.refresh();
      setState({ mode: "idle" });
    } catch (err) {
      setState({
        mode: "error",
        message: err instanceof Error ? err.message : "Unknown error",
      });
    }
  }

  const inputEntries = Object.entries(parent.inputs ?? {});

  return (
    <div className="space-y-5">
      <div className="flex justify-end gap-2">
        <ButtonGhost onClick={() => router.refresh()}>Refresh</ButtonGhost>
        {cancelable ? (
          <ButtonGhost onClick={handleCancel}>
            {state.mode === "cancelling" ? "Cancelling…" : "Cancel fleet run"}
          </ButtonGhost>
        ) : null}
      </div>

      {state.mode === "error" ? (
        <div className="rounded-md border border-coral/40 bg-coral/10 px-4 py-3 text-sm text-coral">
          {state.message}
        </div>
      ) : null}

      {inputEntries.length > 0 ? (
        <Card>
          <CardHeader
            title="Inputs"
            subtitle="Frozen at dispatch time — same values were sent to every child."
          />
          <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {inputEntries.map(([k, v]) => (
              <div
                key={k}
                className="rounded-md border border-white/10 bg-white/[0.02] px-3 py-2"
              >
                <dt className="font-mono text-[10px] uppercase tracking-widest text-white/45">
                  {k}
                </dt>
                <dd className="mt-0.5 break-all font-mono text-xs text-white">
                  {v}
                </dd>
              </div>
            ))}
          </dl>
        </Card>
      ) : null}

      <Card padded={false}>
        <div className="flex items-center justify-between gap-3 px-5 py-3">
          <h3 className="font-display text-base font-bold text-white">
            Children ({children.length})
          </h3>
          <span className="text-[11px] text-white/55">
            One row per dispatched AgentRequest.
          </span>
        </div>
        {children.length === 0 ? (
          <div className="border-t border-white/10 px-5 py-8 text-center text-xs text-white/55">
            No children dispatched — all repos landed on the rejections
            list below.
          </div>
        ) : (
          <ul className="divide-y divide-white/[0.08] border-t border-white/10">
            {children.map((child) => (
              <ChildRow key={child.id} child={child} />
            ))}
          </ul>
        )}
      </Card>

      {rejections.length > 0 ? (
        <Card padded={false}>
          <div className="flex items-center justify-between gap-3 px-5 py-3">
            <h3 className="font-display text-base font-bold text-white">
              Rejections ({rejections.length})
            </h3>
            <span className="text-[11px] text-white/55">
              Best-effort — fix these and re-run just the failed repos.
            </span>
          </div>
          <ul className="divide-y divide-white/[0.08] border-t border-white/10">
            {rejections.map((r, idx) => (
              <RejectionRow key={`${r.repo_id}-${idx}`} rejection={r} />
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}

function ChildRow({ child }: { child: ApiAgentRequest }) {
  const inner = (
    <div className="flex items-start justify-between gap-3 px-5 py-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-white">
            {child.repo_full_name}
          </span>
          <Badge tone={childStatusTone(child.status)} dot>
            {child.status}
          </Badge>
        </div>
        {child.summary ? (
          <p className="mt-1 line-clamp-2 text-[11px] text-coral">
            {child.summary}
          </p>
        ) : null}
      </div>
      {child.gh_html_url ? (
        <span className="shrink-0 text-[11px] text-aqua">Actions run →</span>
      ) : (
        <span className="shrink-0 text-[11px] text-white/35">no run yet</span>
      )}
    </div>
  );
  if (child.gh_html_url) {
    return (
      <li>
        <a
          href={child.gh_html_url}
          target="_blank"
          rel="noreferrer"
          className="block transition hover:bg-white/[0.03]"
        >
          {inner}
        </a>
      </li>
    );
  }
  return <li>{inner}</li>;
}

function RejectionRow({ rejection }: { rejection: ApiFleetRequestRejection }) {
  return (
    <li className="px-5 py-3">
      <div className="flex flex-wrap items-center gap-2">
        {rejection.repo_full_name ? (
          <span className="font-mono text-xs text-white">
            {rejection.repo_full_name}
          </span>
        ) : (
          <span className="font-mono text-xs text-white/60">
            {rejection.repo_id}
          </span>
        )}
        <Badge tone="err">{rejection.code}</Badge>
      </div>
      <p className="mt-1 text-[11px] text-white/70">{rejection.message}</p>
    </li>
  );
}

function childStatusTone(
  status: string,
): "ok" | "warn" | "err" | "neutral" | "info" {
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
