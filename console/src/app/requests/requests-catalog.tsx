"use client";

import { useMemo, useState } from "react";

import { Badge, Card, CardHeader } from "@/components/ui";
import type {
  ApiActivatedRepo,
  ApiCatalogPattern,
  ApiPatternInput,
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

type CardState =
  | { mode: "idle" }
  | { mode: "open" }
  | { mode: "saving" }
  | { mode: "error"; message: string; code?: string };

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
}: {
  workspaceId: string;
  repos: ApiActivatedRepo[];
  patterns: ApiCatalogPattern[];
}) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [cardState, setCardState] = useState<CardState>({ mode: "idle" });
  const [repoId, setRepoId] = useState<string>(repos[0]?.id ?? "");

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
              <PatternCard
                key={p.id}
                pattern={p}
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

function PatternCard({
  pattern,
  expanded,
  state,
  onToggle,
  onSubmit,
}: {
  pattern: ApiCatalogPattern;
  expanded: boolean;
  state: CardState;
  onToggle: () => void;
  onSubmit: (inputs: Record<string, string>) => void;
}) {
  const title = pattern.name ?? pattern.id;
  const summary = pattern.description || pattern.id;
  const tags = [pattern.category, ...pattern.tags.slice(0, 2)].filter(
    (v): v is string => !!v,
  );

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
          <p className="truncate text-sm font-semibold text-white">{title}</p>
          <p className="mt-0.5 line-clamp-2 text-[11px] text-white/55">
            {summary}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            {tags.map((t) => (
              <Badge key={t} tone="neutral">
                {t}
              </Badge>
            ))}
            <span className="font-mono text-[10px] text-white/40">
              {pattern.id}
            </span>
          </div>
        </div>
        <button
          type="button"
          onClick={onToggle}
          className={
            "shrink-0 rounded-md border px-3 py-1 text-[11px] font-semibold transition " +
            (expanded
              ? "border-white/25 bg-white/[0.06] text-white/75 hover:bg-white/[0.10]"
              : "border-aqua/50 bg-aqua/15 text-aqua hover:bg-aqua/25")
          }
        >
          {expanded ? "Cancel" : "Run"}
        </button>
      </div>

      {expanded ? (
        <PatternForm pattern={pattern} state={state} onSubmit={onSubmit} />
      ) : null}
    </div>
  );
}

function PatternForm({
  pattern,
  state,
  onSubmit,
}: {
  pattern: ApiCatalogPattern;
  state: CardState;
  onSubmit: (inputs: Record<string, string>) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(() => {
    const seeded: Record<string, string> = {};
    for (const input of pattern.inputs) {
      if (typeof input.default === "string") {
        seeded[input.name] = input.default;
      }
    }
    return seeded;
  });

  const missing = pattern.inputs
    .filter((i) => i.required && !(values[i.name] ?? "").trim())
    .map((i) => i.name);

  const canSubmit = state.mode !== "saving" && missing.length === 0;

  return (
    <form
      className="mt-4 space-y-3 border-t border-white/10 pt-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (!canSubmit) return;
        const cleaned: Record<string, string> = {};
        for (const [k, v] of Object.entries(values)) {
          const trimmed = v.trim();
          if (trimmed) cleaned[k] = trimmed;
        }
        onSubmit(cleaned);
      }}
    >
      {pattern.inputs.length === 0 ? (
        <p className="text-[11px] text-white/55">
          This pattern doesn&rsquo;t take any inputs — hit Dispatch to
          run it against the selected repo.
        </p>
      ) : (
        pattern.inputs.map((input) => (
          <InputField
            key={input.name}
            input={input}
            value={values[input.name] ?? ""}
            onChange={(next) =>
              setValues((prev) => ({ ...prev, [input.name]: next }))
            }
          />
        ))
      )}

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
  );
}

function InputField({
  input,
  value,
  onChange,
}: {
  input: ApiPatternInput;
  value: string;
  onChange: (next: string) => void;
}) {
  const kind = (input.type ?? "text").toLowerCase();
  const label =
    input.name +
    (input.required ? " *" : "");

  if (kind === "enum" && Array.isArray(input.values) && input.values.length > 0) {
    return (
      <Field label={label} hint={input.hint}>
        <select
          value={value || input.default || ""}
          onChange={(e) => onChange(e.target.value)}
          className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 text-sm text-white focus:border-aqua focus:outline-none"
        >
          {!input.required ? <option value="">(unset)</option> : null}
          {input.values.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      </Field>
    );
  }

  if (kind === "multiline") {
    return (
      <Field label={label} hint={input.hint}>
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={4}
          className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-2 font-mono text-[13px] leading-relaxed text-white focus:border-aqua focus:outline-none"
        />
      </Field>
    );
  }

  return (
    <Field label={label} hint={input.hint}>
      <input
        type={kind === "url" ? "url" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={input.default ?? ""}
        className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 font-mono text-sm text-white focus:border-aqua focus:outline-none"
      />
    </Field>
  );
}

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
