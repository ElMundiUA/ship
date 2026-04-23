"use client";

import { useMemo, useState } from "react";

import {
  PlayCard,
  resolvePlayMode,
  type CardState as PlayCardState,
} from "@/components/play-card";
import { Card, CardHeader } from "@/components/ui";
import type {
  ApiActivatedRepo,
  ApiCatalogPattern,
} from "@/lib/api/client";

/**
 * Requests catalog grid + dynamic form (RFC-0008 C4).
 *
 * The grid groups patterns by ``category`` (role / flow / scan / op
 * / onboard), each card shows summary + badges; a single card can be
 * expanded at a time and renders a form built from
 * ``pattern.spec.inputs``. A separate "Ad-hoc prompt" card at the
 * end keeps the free-form dispatch path alive for cases nothing in
 * the catalog matches.
 *
 * On submit we POST to ``/api/requests`` (the Next.js proxy) and
 * reload the page so "Recent requests" picks up the new row.
 */

type CardState = PlayCardState;

const ADHOC_KEY = "__adhoc__";

const CATEGORY_LABELS: Record<string, string> = {
  role: "Roles",
  flow: "Flows",
  scan: "Scans",
  op: "Operations",
  onboard: "Onboarding",
  common: "Shared",
};

const CATEGORY_ORDER = ["role", "flow", "scan", "op", "onboard"];

const AGENT_CHOICES: { slug: string; label: string; hint: string }[] = [
  { slug: "claude", label: "Claude", hint: "Anthropic — general reasoning." },
  { slug: "gpt", label: "GPT", hint: "OpenAI — general reasoning." },
  { slug: "gemini", label: "Gemini", hint: "Google — code + long context." },
  {
    slug: "custom",
    label: "Custom",
    hint: "Routes to whatever ``shipctl run-adhoc`` maps in the repo.",
  },
];

export function RequestsCatalog({
  workspaceId,
  repos,
  patterns,
  lockedRepoId,
}: {
  workspaceId: string;
  repos: ApiActivatedRepo[];
  patterns: ApiCatalogPattern[];
  /**
   * Repo-mode locks the dropdown to a specific repo. When set, the
   * repo selector is hidden and every dispatched request targets
   * ``lockedRepoId``. In workspace-mode this stays ``undefined`` so
   * the user picks freely from ``repos``.
   */
  lockedRepoId?: string;
}) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [cardState, setCardState] = useState<CardState>({ mode: "idle" });
  const [repoId, setRepoId] = useState<string>(
    lockedRepoId ?? repos[0]?.id ?? "",
  );

  const grouped = useMemo(() => {
    const byCategory = new Map<string, ApiCatalogPattern[]>();
    for (const p of patterns) {
      // Shared fragments never come back via ``?mode=request`` but
      // guard anyway so a legacy entry doesn't spill into the grid.
      if (p.category === "common") continue;
      const bucket = p.category || "role";
      const list = byCategory.get(bucket) ?? [];
      list.push(p);
      byCategory.set(bucket, list);
    }
    for (const list of byCategory.values()) {
      list.sort((a, b) =>
        (a.name ?? a.id).localeCompare(b.name ?? b.id),
      );
    }
    const ordered: { category: string; label: string; patterns: ApiCatalogPattern[] }[] = [];
    for (const cat of CATEGORY_ORDER) {
      const list = byCategory.get(cat);
      if (list && list.length > 0) {
        ordered.push({
          category: cat,
          label: CATEGORY_LABELS[cat] ?? cat,
          patterns: list,
        });
      }
    }
    for (const [cat, list] of byCategory.entries()) {
      if (!CATEGORY_ORDER.includes(cat)) {
        ordered.push({
          category: cat,
          label: CATEGORY_LABELS[cat] ?? cat,
          patterns: list,
        });
      }
    }
    return ordered;
  }, [patterns]);

  if (repos.length === 0) {
    return (
      <Card>
        <CardHeader
          title="Activate a repo first"
          subtitle="Requests dispatch against a specific repo. Finish onboarding to enable the grid."
        />
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="New request"
          subtitle="Pick a pattern below — each one ships with the inputs it needs."
        />
        {lockedRepoId ? (
          <p className="mt-4 text-[11px] text-white/55">
            Requests dispatch against{" "}
            <span className="font-mono text-white/80">
              {repos.find((r) => r.id === lockedRepoId)?.full_name ?? "this repo"}
            </span>
            &rsquo;s default branch.
          </p>
        ) : (
          <div className="mt-4">
            <label className="block text-[10px] font-semibold uppercase tracking-widest text-white/55">
              Repo
            </label>
            <select
              value={repoId}
              onChange={(e) => setRepoId(e.target.value)}
              className="mt-1 w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 text-sm text-white focus:border-aqua focus:outline-none"
            >
              {repos.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.full_name}
                </option>
              ))}
            </select>
            <p className="mt-1 text-[11px] text-white/45">
              Request fires against this repo&rsquo;s default branch.
            </p>
          </div>
        )}
      </Card>

      {grouped.length === 0 ? (
        <Card>
          <CardHeader
            title="Catalog is empty"
            subtitle="No request-mode patterns are available right now. Run `shipctl sync` or check your catalog."
          />
        </Card>
      ) : null}

      {grouped.map((group) => (
        <section key={group.category}>
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-widest text-white/55">
            {group.label}
          </h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {group.patterns.map((p) => (
              <PlayCard
                key={p.id}
                id={p.id}
                title={p.name ?? p.id}
                description={p.description || p.id}
                tags={[p.category, ...p.tags.slice(0, 2)].filter(
                  (v): v is string => !!v,
                )}
                mode={resolvePlayMode(p.modes)}
                pattern={p}
                ctaLayout={{
                  // Every catalog pattern on /requests has ``modes:
                  // [request]`` (we filter to that mode upstream), so
                  // both CTAs render. Lane-only Plays only show on
                  // /plays where the page passes a different layout.
                  showRunNow: true,
                  showAutomate: true,
                }}
                expanded={openKey === p.id}
                state={openKey === p.id ? cardState : { mode: "idle" }}
                onToggle={() => {
                  setOpenKey(openKey === p.id ? null : p.id);
                  setCardState({ mode: "idle" });
                }}
                onSubmit={async (inputs) => {
                  setCardState({ mode: "saving" });
                  try {
                    const res = await fetch("/api/requests", {
                      method: "POST",
                      headers: { "content-type": "application/json" },
                      body: JSON.stringify({
                        workspaceId,
                        repoId,
                        pattern_id: p.id,
                        inputs,
                      }),
                    });
                    const data = (await res.json()) as {
                      id?: string;
                      error?: string;
                      code?: string;
                    };
                    if (!res.ok || !data.id) {
                      setCardState({
                        mode: "error",
                        message: data.error || `HTTP ${res.status}`,
                        code: data.code,
                      });
                      return;
                    }
                    window.location.reload();
                  } catch (err) {
                    setCardState({
                      mode: "error",
                      message: err instanceof Error ? err.message : "Unknown error",
                    });
                  }
                }}
              />
            ))}
          </div>
        </section>
      ))}

      <section>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-widest text-white/55">
          Escape hatch
        </h3>
        <AdhocCard
          expanded={openKey === ADHOC_KEY}
          state={openKey === ADHOC_KEY ? cardState : { mode: "idle" }}
          onToggle={() => {
            setOpenKey(openKey === ADHOC_KEY ? null : ADHOC_KEY);
            setCardState({ mode: "idle" });
          }}
          onSubmit={async ({ agent_slug, prompt, context_ref }) => {
            setCardState({ mode: "saving" });
            try {
              const res = await fetch("/api/requests", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({
                  workspaceId,
                  repoId,
                  agent_slug,
                  prompt,
                  context_ref: context_ref || undefined,
                }),
              });
              const data = (await res.json()) as {
                id?: string;
                error?: string;
                code?: string;
              };
              if (!res.ok || !data.id) {
                setCardState({
                  mode: "error",
                  message: data.error || `HTTP ${res.status}`,
                  code: data.code,
                });
                return;
              }
              window.location.reload();
            } catch (err) {
              setCardState({
                mode: "error",
                message: err instanceof Error ? err.message : "Unknown error",
              });
            }
          }}
        />
      </section>
    </div>
  );
}

// ``PatternCard`` / ``PatternForm`` / ``InputField`` used to live
// here; P1-10 + P1-11 extracted them into the shared
// ``components/play-card.tsx`` so ``/plays`` and ``/requests`` can
// render the same card. ``AdhocCard`` stays local because it's
// request-only (no Plays equivalent).

function AdhocCard({
  expanded,
  state,
  onToggle,
  onSubmit,
}: {
  expanded: boolean;
  state: CardState;
  onToggle: () => void;
  onSubmit: (body: {
    agent_slug: string;
    prompt: string;
    context_ref: string;
  }) => void;
}) {
  const [agentSlug, setAgentSlug] = useState<string>(AGENT_CHOICES[0].slug);
  const [contextRef, setContextRef] = useState<string>("");
  const [prompt, setPrompt] = useState<string>("");
  const canSubmit = state.mode !== "saving" && !!agentSlug && prompt.trim().length > 0;

  return (
    <div
      className={
        "rounded-lg border bg-white/[0.02] p-3 transition " +
        (expanded
          ? "border-aqua/40 bg-aqua/[0.04]"
          : "border-white/10 hover:border-white/25")
      }
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-white">Ad-hoc prompt</p>
          <p className="mt-0.5 text-[11px] text-white/55">
            Free-form dispatch when nothing in the catalog fits. Picks
            the agent directly and forwards a raw prompt.
          </p>
        </div>
        <button
          type="button"
          onClick={onToggle}
          className={
            "shrink-0 rounded-md border px-3 py-1 text-[11px] font-semibold transition " +
            (expanded
              ? "border-white/25 bg-white/[0.06] text-white/75 hover:bg-white/[0.10]"
              : "border-white/20 bg-white/[0.03] text-white/75 hover:border-white/35")
          }
        >
          {expanded ? "Cancel" : "Open"}
        </button>
      </div>

      {expanded ? (
        <form
          className="mt-4 space-y-3 border-t border-white/10 pt-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!canSubmit) return;
            onSubmit({ agent_slug: agentSlug, prompt, context_ref: contextRef });
          }}
        >
          <Field
            label="Agent"
            hint={AGENT_CHOICES.find((a) => a.slug === agentSlug)?.hint}
          >
            <select
              value={agentSlug}
              onChange={(e) => setAgentSlug(e.target.value)}
              className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 text-sm text-white focus:border-aqua focus:outline-none"
            >
              {AGENT_CHOICES.map((a) => (
                <option key={a.slug} value={a.slug}>
                  {a.label}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Context (optional)"
            hint="Ticket URL, PR URL, file path — forwarded as ``inputs.context_ref``."
          >
            <input
              type="text"
              value={contextRef}
              onChange={(e) => setContextRef(e.target.value)}
              placeholder="https://linear.app/… or src/foo.py"
              className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 font-mono text-sm text-white focus:border-aqua focus:outline-none"
            />
          </Field>
          <Field label="Prompt *" hint="Forwarded as ``inputs.prompt``.">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={6}
              placeholder="Audit the PR for regressions in the payments flow and draft a review."
              className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-2 font-mono text-[13px] leading-relaxed text-white focus:border-aqua focus:outline-none"
            />
          </Field>

          {state.mode === "error" ? (
            <div className="rounded-md border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
              {state.message}
            </div>
          ) : null}

          <div className="flex items-center justify-end gap-2">
            <button
              type="submit"
              disabled={!canSubmit}
              className={
                "rounded-md border px-4 py-1.5 text-xs font-semibold transition " +
                (canSubmit
                  ? "border-aqua/50 bg-aqua/15 text-aqua hover:bg-aqua/25"
                  : "cursor-not-allowed border-white/15 bg-white/[0.04] text-white/45")
              }
            >
              {state.mode === "saving" ? "Dispatching…" : "Dispatch"}
            </button>
          </div>
        </form>
      ) : null}
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
      <label className="block text-[10px] font-semibold uppercase tracking-widest text-white/55">
        {label}
      </label>
      <div className="mt-1">{children}</div>
      {hint ? <p className="mt-1 text-[11px] text-white/45">{hint}</p> : null}
    </div>
  );
}
