"use client";

import { useState } from "react";

import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
} from "@/components/ui";
import type {
  ApiCustomPattern,
  ApiPatternDraft,
} from "@/lib/api/client";

/**
 * AI-backed author for workspace-private catalog patterns
 * (RFC-0008 §H / PR-6).
 *
 * The modal opens in two stages:
 *
 *   1. **Brief** — operator types a short description of what the
 *      pattern should do; hits "Generate draft". We POST to
 *      ``/api/patterns/draft`` and render the returned
 *      :class:`ApiPatternDraft` inline for review.
 *   2. **Review & save** — operator eyeballs / edits the draft and
 *      hits "Save pattern". We POST to ``/api/patterns`` and bubble
 *      the persisted :class:`ApiCustomPattern` up via
 *      ``onPatternSaved`` so the parent picker can refresh itself
 *      without a full page reload.
 *
 * Stays deliberately minimal: editing happens in a handful of
 * controlled inputs + a body ``<textarea>``. For richer structured
 * input editing we'd build a dedicated author page — but PR-6's
 * point is "unblock operators when the baked-in catalog is missing
 * something", and a text-level tweak is enough to ship.
 */

type Stage =
  | { kind: "brief" }
  | { kind: "loading_draft" }
  | { kind: "review"; draft: ApiPatternDraft }
  | { kind: "saving"; draft: ApiPatternDraft }
  | { kind: "error"; message: string; code?: string; fallback?: ApiPatternDraft };

const MODES: readonly ("lane" | "request")[] = ["lane", "request"];

export function PatternAiAuthor({
  workspaceId,
  defaultMode,
  onPatternSaved,
  triggerLabel = "+ Generate with AI",
}: {
  workspaceId: string;
  /** Bias the draft toward a mode (used by /fleet/lanes/new → "lane"). */
  defaultMode?: "lane" | "request";
  /** Fired after a successful save. Parent refetches the catalog. */
  onPatternSaved: (pattern: ApiCustomPattern) => void;
  triggerLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [brief, setBrief] = useState("");
  const [targetModes, setTargetModes] = useState<("lane" | "request")[]>(
    defaultMode ? [defaultMode] : ["lane", "request"],
  );
  const [stage, setStage] = useState<Stage>({ kind: "brief" });

  const reset = () => {
    setBrief("");
    setTargetModes(defaultMode ? [defaultMode] : ["lane", "request"]);
    setStage({ kind: "brief" });
  };

  const close = () => {
    setOpen(false);
    reset();
  };

  const generate = async () => {
    if (brief.trim().length < 8) {
      setStage({
        kind: "error",
        message: "Describe the pattern in a sentence or two first.",
      });
      return;
    }
    setStage({ kind: "loading_draft" });
    try {
      const resp = await fetch("/api/patterns/draft", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspaceId,
          prompt: brief.trim(),
          target_modes: targetModes,
        }),
      });
      if (!resp.ok) {
        const payload = (await resp.json().catch(() => ({}))) as {
          error?: string;
          code?: string;
        };
        setStage({
          kind: "error",
          message:
            payload.error ?? `Draft failed (HTTP ${resp.status}).`,
          code: payload.code,
        });
        return;
      }
      const draft = (await resp.json()) as ApiPatternDraft;
      setStage({ kind: "review", draft });
    } catch (err) {
      setStage({
        kind: "error",
        message:
          err instanceof Error ? err.message : "Draft failed with an unknown error.",
      });
    }
  };

  const save = async (draft: ApiPatternDraft) => {
    setStage({ kind: "saving", draft });
    try {
      const resp = await fetch("/api/patterns", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspaceId,
          pattern_id: draft.pattern_id,
          name: draft.name,
          description: draft.description,
          category: draft.category,
          modes: draft.modes,
          inputs: draft.inputs,
          spec: draft.spec,
          body: draft.body,
        }),
      });
      if (!resp.ok) {
        const payload = (await resp.json().catch(() => ({}))) as {
          error?: string;
          code?: string;
        };
        setStage({
          kind: "error",
          message:
            payload.error ?? `Save failed (HTTP ${resp.status}).`,
          code: payload.code,
          fallback: draft,
        });
        return;
      }
      const saved = (await resp.json()) as ApiCustomPattern;
      onPatternSaved(saved);
      close();
    } catch (err) {
      setStage({
        kind: "error",
        message:
          err instanceof Error ? err.message : "Save failed with an unknown error.",
        fallback: draft,
      });
    }
  };

  if (!open) {
    return <ButtonGhost onClick={() => setOpen(true)}>{triggerLabel}</ButtonGhost>;
  }

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/60 px-4 py-10 backdrop-blur-sm">
      <div className="w-full max-w-3xl">
        <Card>
          <CardHeader
            title="Author a new pattern"
            subtitle={
              stage.kind === "review" || stage.kind === "saving"
                ? "Review the draft, edit anything that looks off, then save."
                : "Describe what the pattern should do. We'll sketch a draft you can edit."
            }
            action={<ButtonGhost onClick={close}>Close</ButtonGhost>}
          />

          {stage.kind === "brief" || stage.kind === "loading_draft" ? (
            <BriefStage
              brief={brief}
              onBrief={setBrief}
              targetModes={targetModes}
              onToggleMode={(m) => {
                setTargetModes((prev) =>
                  prev.includes(m)
                    ? prev.filter((x) => x !== m)
                    : [...prev, m],
                );
              }}
              onGenerate={generate}
              loading={stage.kind === "loading_draft"}
            />
          ) : null}

          {(stage.kind === "review" || stage.kind === "saving") ? (
            <ReviewStage
              draft={stage.draft}
              onChange={(next) => setStage({ kind: "review", draft: next })}
              onSave={() => save(stage.draft)}
              onBack={reset}
              saving={stage.kind === "saving"}
            />
          ) : null}

          {stage.kind === "error" ? (
            <div className="space-y-3">
              <div className="rounded-md border border-coral/40 bg-coral/10 px-4 py-3 text-sm text-coral">
                {stage.message}
                {stage.code ? (
                  <span className="ml-2 font-mono text-[11px] opacity-75">
                    ({stage.code})
                  </span>
                ) : null}
              </div>
              <div className="flex gap-2">
                <ButtonGhost onClick={reset}>Start over</ButtonGhost>
                {stage.fallback ? (
                  <ButtonGhost
                    onClick={() =>
                      setStage({ kind: "review", draft: stage.fallback! })
                    }
                  >
                    Back to draft
                  </ButtonGhost>
                ) : null}
              </div>
            </div>
          ) : null}
        </Card>
      </div>
    </div>
  );
}

function BriefStage({
  brief,
  onBrief,
  targetModes,
  onToggleMode,
  onGenerate,
  loading,
}: {
  brief: string;
  onBrief: (v: string) => void;
  targetModes: ("lane" | "request")[];
  onToggleMode: (m: "lane" | "request") => void;
  onGenerate: () => void;
  loading: boolean;
}) {
  return (
    <div className="space-y-4">
      <div>
        <label className="mb-1.5 block text-xs font-semibold text-white/65">
          What should the pattern do?
        </label>
        <textarea
          value={brief}
          onChange={(e) => onBrief(e.target.value)}
          rows={5}
          placeholder="e.g. Every Monday, scan the repo for outdated TypeScript dependencies and open a PR that bumps anything more than 2 majors behind latest."
          className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-2 text-sm text-white placeholder:text-white/30 focus:border-aqua focus:outline-none"
        />
        <p className="mt-1.5 text-[11px] text-white/45">
          Be specific about inputs, triggers, and the success criteria — the model
          leans on the brief for almost everything.
        </p>
      </div>

      <div>
        <p className="mb-1.5 text-xs font-semibold text-white/65">
          Usage modes
        </p>
        <div className="flex gap-1.5">
          {MODES.map((m) => {
            const on = targetModes.includes(m);
            return (
              <button
                key={m}
                type="button"
                onClick={() => onToggleMode(m)}
                className={
                  "rounded-full border px-3 py-1 text-[11px] font-semibold transition " +
                  (on
                    ? "border-aqua/60 bg-aqua/10 text-white"
                    : "border-white/15 bg-white/[0.03] text-white/55 hover:border-white/30")
                }
              >
                {m}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex items-center justify-end">
        <ButtonPrimary onClick={onGenerate}>
          {loading ? "Generating…" : "Generate draft"}
        </ButtonPrimary>
      </div>
    </div>
  );
}

function ReviewStage({
  draft,
  onChange,
  onSave,
  onBack,
  saving,
}: {
  draft: ApiPatternDraft;
  onChange: (next: ApiPatternDraft) => void;
  onSave: () => void;
  onBack: () => void;
  saving: boolean;
}) {
  const set = <K extends keyof ApiPatternDraft>(key: K, value: ApiPatternDraft[K]) =>
    onChange({ ...draft, [key]: value });

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Pattern id" hint="Lowercase, hyphens only.">
          <input
            type="text"
            value={draft.pattern_id}
            onChange={(e) => set("pattern_id", e.target.value)}
            className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 font-mono text-sm text-white focus:border-aqua focus:outline-none"
          />
        </Field>
        <Field label="Name" hint="Shown in pickers.">
          <input
            type="text"
            value={draft.name}
            onChange={(e) => set("name", e.target.value)}
            className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 text-sm text-white focus:border-aqua focus:outline-none"
          />
        </Field>
      </div>

      <Field label="Description" hint="One sentence; appears in picker cards.">
        <input
          type="text"
          value={draft.description}
          onChange={(e) => set("description", e.target.value)}
          className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 text-sm text-white focus:border-aqua focus:outline-none"
        />
      </Field>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-white/65">Modes:</span>
        {MODES.map((m) => {
          const on = draft.modes.includes(m);
          return (
            <button
              key={m}
              type="button"
              onClick={() =>
                set(
                  "modes",
                  on
                    ? draft.modes.filter((x) => x !== m)
                    : [...draft.modes, m],
                )
              }
              className={
                "rounded-full border px-3 py-1 text-[11px] font-semibold transition " +
                (on
                  ? "border-aqua/60 bg-aqua/10 text-white"
                  : "border-white/15 bg-white/[0.03] text-white/55 hover:border-white/30")
              }
            >
              {m}
            </button>
          );
        })}
        {draft.category ? (
          <Badge tone="neutral">{draft.category}</Badge>
        ) : null}
      </div>

      <Field
        label="Inputs"
        hint="JSON array. Each input's id is referenced in the body as ${id}."
      >
        <textarea
          value={JSON.stringify(draft.inputs, null, 2)}
          onChange={(e) => {
            try {
              const parsed = JSON.parse(e.target.value);
              if (Array.isArray(parsed)) {
                set("inputs", parsed);
              }
            } catch {
              // Ignore parse errors mid-typing; the operator fixes the
              // JSON before save. We render whatever the LLM emitted
              // on first paint so 99% of the time no editing is needed.
            }
          }}
          rows={Math.max(3, Math.min(10, draft.inputs.length * 3 + 2))}
          className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 font-mono text-xs text-white focus:border-aqua focus:outline-none"
        />
      </Field>

      <Field label="Body" hint="Markdown prompt the agent executes. Use ${input_id} placeholders.">
        <textarea
          value={draft.body}
          onChange={(e) => set("body", e.target.value)}
          rows={12}
          className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-2 font-mono text-xs text-white focus:border-aqua focus:outline-none"
        />
      </Field>

      <div className="flex items-center justify-between border-t border-white/10 pt-4">
        <ButtonGhost onClick={onBack}>Regenerate</ButtonGhost>
        <ButtonPrimary onClick={onSave}>
          {saving ? "Saving…" : "Save pattern"}
        </ButtonPrimary>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-semibold text-white/65">
        {label}
      </label>
      {children}
      {hint ? <p className="mt-1 text-[11px] text-white/45">{hint}</p> : null}
    </div>
  );
}
