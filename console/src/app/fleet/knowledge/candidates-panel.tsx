"use client";

/**
 * Promote-candidates tab body (PR-7B).
 *
 * Fetches cross-repo dedup clusters from
 * ``/api/knowledge/candidates`` and renders each as a card with its
 * cluster preview + a "Draft with AI" button that opens
 * :mod:`promotion-modal`. A successful promotion closes the modal
 * and re-fetches the list (server-side invalidation drops the
 * promoted cluster from the cache, so the refreshed list reflects
 * it immediately).
 */

import { useCallback, useEffect, useState } from "react";

import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  EmptyState,
} from "@/components/ui";
import type {
  ApiKnowledgeCandidate,
  ApiKnowledgeCandidatesResponse,
} from "@/lib/api/client";

import { PromotionModal } from "./promotion-modal";

type Props = { workspaceId: string };

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; data: ApiKnowledgeCandidatesResponse }
  | { kind: "error"; error: string };

export function CandidatesPanel({ workspaceId }: Props) {
  const [state, setState] = useState<LoadState>({ kind: "idle" });
  const [refreshing, setRefreshing] = useState(false);
  const [selected, setSelected] = useState<ApiKnowledgeCandidate | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const res = await fetch(
        `/api/knowledge/candidates?workspaceId=${encodeURIComponent(workspaceId)}`,
        { cache: "no-store" },
      );
      const body = (await res.json()) as
        | ApiKnowledgeCandidatesResponse
        | { error?: string };
      if (!res.ok) {
        const msg =
          ("error" in body && body.error) ||
          `Failed to load candidates (HTTP ${res.status}).`;
        setState({ kind: "error", error: msg });
        return;
      }
      setState({ kind: "ready", data: body as ApiKnowledgeCandidatesResponse });
    } catch (err) {
      setState({
        kind: "error",
        error: err instanceof Error ? err.message : "Unknown error",
      });
    }
  }, [workspaceId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleRefresh() {
    setRefreshing(true);
    try {
      const res = await fetch("/api/knowledge/candidates", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ workspaceId }),
      });
      const body = (await res.json()) as
        | ApiKnowledgeCandidatesResponse
        | { error?: string };
      if (!res.ok) {
        const msg =
          ("error" in body && body.error) ||
          `Refresh failed (HTTP ${res.status}).`;
        setState({ kind: "error", error: msg });
        return;
      }
      setState({ kind: "ready", data: body as ApiKnowledgeCandidatesResponse });
    } catch (err) {
      setState({
        kind: "error",
        error: err instanceof Error ? err.message : "Unknown error",
      });
    } finally {
      setRefreshing(false);
    }
  }

  function handlePromoted(result: { slug: string }) {
    setSelected(null);
    setSuccessMessage(
      `Promoted to workspace bucket "${result.slug}". Source articles updated.`,
    );
    void load();
  }

  const data = state.kind === "ready" ? state.data : null;
  const candidates = data?.candidates ?? [];

  return (
    <div className="flex flex-col gap-4">
      <Card padded={false}>
        <CardHeader
          className="px-5 pt-5"
          title="Promotion candidates"
          subtitle="Repo-scope articles that look like duplicates across repos. Draft and promote one canonical per cluster."
          action={
            <div className="flex items-center gap-2">
              {data && (
                <Badge tone={data.is_fresh ? "ok" : "info"}>
                  {data.is_fresh ? "cache hit" : "recomputed"}
                </Badge>
              )}
              <ButtonGhost onClick={() => void handleRefresh()}>
                {refreshing ? "Refreshing…" : "Refresh"}
              </ButtonGhost>
            </div>
          }
        />

        {successMessage && (
          <div className="mx-5 mb-3 rounded-lg border border-emerald-400/40 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-200">
            {successMessage}
          </div>
        )}

        {state.kind === "loading" && (
          <p className="px-5 pb-5 text-sm text-white/60">
            Loading candidates…
          </p>
        )}

        {state.kind === "error" && (
          <p className="px-5 pb-5 text-sm text-coral">{state.error}</p>
        )}

        {state.kind === "ready" && candidates.length === 0 && (
          <div className="px-5 pb-5">
            <EmptyState
              title="No candidates"
              body="No cross-repo duplicates detected right now. Run Refresh after your next KB reindex to recompute."
            />
          </div>
        )}

        {state.kind === "ready" && candidates.length > 0 && (
          <ul className="divide-y divide-white/5">
            {candidates.map((c) => (
              <CandidateRow
                key={c.id}
                candidate={c}
                onDraft={() => {
                  setSuccessMessage(null);
                  setSelected(c);
                }}
              />
            ))}
          </ul>
        )}
      </Card>

      {selected && (
        <PromotionModal
          workspaceId={workspaceId}
          candidate={selected}
          onClose={() => setSelected(null)}
          onPromoted={handlePromoted}
        />
      )}
    </div>
  );
}

function CandidateRow({
  candidate,
  onDraft,
}: {
  candidate: ApiKnowledgeCandidate;
  onDraft: () => void;
}) {
  const similarityPct = Math.round((candidate.centroid_score || 0) * 100);
  return (
    <li className="px-5 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm font-semibold text-white">
              {candidate.slug_hint}
            </span>
            <Badge tone="info">{candidate.member_count} articles</Badge>
            <Badge tone="workspace">{candidate.repo_count} repos</Badge>
            <Badge tone="ok">{similarityPct}% similar</Badge>
          </div>
          <ul className="mt-2 flex flex-col gap-1">
            {candidate.members.slice(0, 4).map((m) => (
              <li
                key={m.article_id}
                className="truncate text-xs text-white/65"
              >
                <span className="font-mono text-white/80">
                  {m.repo_full_name ?? "unknown repo"}
                </span>
                <span className="mx-1 text-white/40">·</span>
                <span className="font-mono text-aqua/80">{m.bucket_slug}</span>
                {m.title && (
                  <>
                    <span className="mx-1 text-white/40">·</span>
                    <span>{m.title}</span>
                  </>
                )}
                {m.preview && (
                  <span className="ml-1 text-white/45">— {m.preview}</span>
                )}
              </li>
            ))}
            {candidate.members.length > 4 && (
              <li className="text-[11px] text-white/45">
                +{candidate.members.length - 4} more
              </li>
            )}
          </ul>
        </div>
        <div className="shrink-0">
          <ButtonPrimary onClick={onDraft}>Draft with AI</ButtonPrimary>
        </div>
      </div>
    </li>
  );
}
