"use client";

/**
 * LLM-backed promotion modal (PR-7B).
 *
 * Two stages:
 *
 * 1. **Drafting** — POST ``/api/knowledge/candidates/{id}/draft``
 *    and render a spinner until the model responds. The 412 branch
 *    surfaces the "LLM isn't configured" banner the Console shows
 *    for every other LLM-backed feature.
 * 2. **Review** — editable slug/title/body, read-only summary/notes,
 *    override-mark checkbox (default on), and a Promote button that
 *    POSTs to ``/api/knowledge/promote``. Regenerate loops back to
 *    the drafting stage.
 */

import { useEffect, useState } from "react";

import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
} from "@/components/ui";
import type {
  ApiKnowledgeCandidate,
  ApiKnowledgePromotionDraft,
  ApiKnowledgePromotionResult,
} from "@/lib/api/client";

type Props = {
  workspaceId: string;
  candidate: ApiKnowledgeCandidate;
  onClose: () => void;
  onPromoted: (result: { slug: string }) => void;
};

type Stage =
  | { kind: "drafting" }
  | { kind: "error"; message: string; code?: string }
  | { kind: "review"; draft: ApiKnowledgePromotionDraft };

export function PromotionModal({
  workspaceId,
  candidate,
  onClose,
  onPromoted,
}: Props) {
  const [stage, setStage] = useState<Stage>({ kind: "drafting" });
  const [slug, setSlug] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [markOverrides, setMarkOverrides] = useState(true);
  const [promoting, setPromoting] = useState(false);

  useEffect(() => {
    void runDraft();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function runDraft() {
    setStage({ kind: "drafting" });
    try {
      const res = await fetch(
        `/api/knowledge/candidates/${encodeURIComponent(candidate.id)}/draft`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ workspaceId }),
        },
      );
      const payload = (await res.json()) as
        | ApiKnowledgePromotionDraft
        | { error?: string; code?: string };
      if (!res.ok) {
        const msg =
          ("error" in payload && payload.error) ||
          (res.status === 412
            ? "The workspace LLM isn't configured. Set an API key to draft canonical articles."
            : `Draft failed (HTTP ${res.status}).`);
        setStage({
          kind: "error",
          message: msg,
          code: "code" in payload ? payload.code : undefined,
        });
        return;
      }
      const draft = payload as ApiKnowledgePromotionDraft;
      setSlug(draft.slug || candidate.slug_hint);
      setTitle(draft.title || candidate.slug_hint);
      setBody(draft.body || "");
      setStage({ kind: "review", draft });
    } catch (err) {
      setStage({
        kind: "error",
        message: err instanceof Error ? err.message : "Unknown error",
      });
    }
  }

  async function runPromote() {
    if (stage.kind !== "review") return;
    setPromoting(true);
    try {
      const res = await fetch("/api/knowledge/promote", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspaceId,
          slug: slug.trim(),
          title: title.trim(),
          body: body.trim(),
          summary: stage.draft.summary,
          sourceArticleIds: candidate.members.map((m) => m.article_id),
          markSourcesAsOverrides: markOverrides,
        }),
      });
      const payload = (await res.json()) as
        | ApiKnowledgePromotionResult
        | { error?: string };
      if (!res.ok) {
        const msg =
          ("error" in payload && payload.error) ||
          `Promote failed (HTTP ${res.status}).`;
        setStage({ kind: "error", message: msg });
        return;
      }
      onPromoted({ slug: slug.trim() });
    } catch (err) {
      setStage({
        kind: "error",
        message: err instanceof Error ? err.message : "Unknown error",
      });
    } finally {
      setPromoting(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Draft canonical article"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto">
        <Card padded={false}>
          <CardHeader
            className="px-5 pt-5"
            title="Draft canonical article"
            subtitle={`Cluster: ${candidate.slug_hint} · ${candidate.member_count} articles across ${candidate.repo_count} repos.`}
            action={
              <ButtonGhost onClick={onClose}>Close</ButtonGhost>
            }
          />

          <div className="flex flex-col gap-4 px-5 pb-5">
            {stage.kind === "drafting" && (
              <p className="text-sm text-white/70">Generating canonical…</p>
            )}

            {stage.kind === "error" && (
              <>
                <div className="rounded-lg border border-coral/40 bg-coral/5 px-4 py-3 text-sm text-coral">
                  {stage.message}
                </div>
                <div className="flex items-center gap-2">
                  <ButtonPrimary onClick={() => void runDraft()}>
                    Try again
                  </ButtonPrimary>
                  <ButtonGhost onClick={onClose}>Cancel</ButtonGhost>
                </div>
              </>
            )}

            {stage.kind === "review" && (
              <>
                <label className="flex flex-col gap-1.5">
                  <span className="text-[10px] font-bold uppercase tracking-widest text-white/50">
                    Slug
                  </span>
                  <input
                    type="text"
                    value={slug}
                    onChange={(e) => setSlug(e.target.value)}
                    className="rounded-lg border border-white/15 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none transition focus:border-aqua/60"
                  />
                </label>
                <label className="flex flex-col gap-1.5">
                  <span className="text-[10px] font-bold uppercase tracking-widest text-white/50">
                    Title
                  </span>
                  <input
                    type="text"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    className="rounded-lg border border-white/15 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none transition focus:border-aqua/60"
                  />
                </label>
                <label className="flex flex-col gap-1.5">
                  <span className="text-[10px] font-bold uppercase tracking-widest text-white/50">
                    Body (markdown)
                  </span>
                  <textarea
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    rows={12}
                    className="rounded-lg border border-white/15 bg-white/[0.04] px-3 py-2 font-mono text-xs text-white outline-none transition focus:border-aqua/60"
                  />
                </label>

                {stage.draft.summary && (
                  <div className="flex flex-col gap-1.5">
                    <span className="text-[10px] font-bold uppercase tracking-widest text-white/50">
                      Summary (read-only)
                    </span>
                    <p className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-xs text-white/70">
                      {stage.draft.summary}
                    </p>
                  </div>
                )}

                {stage.draft.notes && (
                  <div className="flex flex-col gap-1.5">
                    <span className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-white/50">
                      Notes from the model
                      <Badge tone="warn">review</Badge>
                    </span>
                    <p className="whitespace-pre-wrap rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-xs text-white/70">
                      {stage.draft.notes}
                    </p>
                  </div>
                )}

                <label className="flex items-center gap-2 text-sm text-white/80">
                  <input
                    type="checkbox"
                    checked={markOverrides}
                    onChange={(e) => setMarkOverrides(e.target.checked)}
                  />
                  Mark source articles as overrides of this canonical
                </label>

                <div className="flex items-center gap-2">
                  <ButtonPrimary onClick={() => void runPromote()}>
                    {promoting ? "Promoting…" : "Promote"}
                  </ButtonPrimary>
                  <ButtonGhost onClick={() => void runDraft()}>
                    Regenerate
                  </ButtonGhost>
                  <ButtonGhost onClick={onClose}>Cancel</ButtonGhost>
                </div>
              </>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
