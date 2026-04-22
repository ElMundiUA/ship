"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { PatternAiAuthor } from "@/components/pattern-ai-author";
import { Badge, ButtonGhost, ButtonPrimary, Card, CardHeader } from "@/components/ui";
import type {
  ApiActivatedRepo,
  ApiCatalogPattern,
  ApiPatternInput,
} from "@/lib/api/client";

/**
 * Client-side form for ``POST /api/fleet/requests``.
 *
 * Two axes, both driven by the same catalog the single-repo form
 * uses:
 *
 * - **Pattern** — grouped by ``category``; clicking a card selects
 *   it and renders the dynamic inputs declared by
 *   ``pattern.spec.inputs``.
 * - **Repos** — multi-select checklist of activated repos, with a
 *   quick filter, select-all, and a live "X of N selected" counter.
 *
 * Submit POSTs ``/api/fleet/requests`` and navigates to the detail
 * view on success. Errors surface inline (422 from the backend
 * includes the ``missing`` list, which we surface so operators can
 * see exactly which input failed).
 */

type FormState =
  | { mode: "idle" }
  | { mode: "saving" }
  | { mode: "error"; message: string; code?: string };

export function FleetRequestForm({
  workspaceId,
  repos,
  patterns: initialPatterns,
}: {
  workspaceId: string;
  repos: ApiActivatedRepo[];
  patterns: ApiCatalogPattern[];
}) {
  const router = useRouter();
  // Local catalog state so a newly-authored pattern (RFC-0008 §H /
  // PR-6 — AI author modal) shows up without a full-page reload.
  const [patterns, setPatterns] = useState<ApiCatalogPattern[]>(initialPatterns);
  const [selectedPatternId, setSelectedPatternId] = useState<string | null>(
    patterns[0]?.id ?? null,
  );
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [selectedRepoIds, setSelectedRepoIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [repoFilter, setRepoFilter] = useState("");
  const [title, setTitle] = useState("");
  const [state, setState] = useState<FormState>({ mode: "idle" });

  const selectedPattern = useMemo(
    () => patterns.find((p) => p.id === selectedPatternId) ?? null,
    [patterns, selectedPatternId],
  );

  const grouped = useMemo(() => {
    const byCategory = new Map<string, ApiCatalogPattern[]>();
    for (const p of patterns) {
      if (p.category === "common") continue;
      const bucket = p.category || "role";
      const list = byCategory.get(bucket) ?? [];
      list.push(p);
      byCategory.set(bucket, list);
    }
    for (const list of byCategory.values()) {
      list.sort((a, b) => (a.name ?? a.id).localeCompare(b.name ?? b.id));
    }
    return Array.from(byCategory.entries()).map(([category, list]) => ({
      category,
      patterns: list,
    }));
  }, [patterns]);

  const filteredRepos = useMemo(() => {
    const q = repoFilter.trim().toLowerCase();
    if (!q) return repos;
    return repos.filter((r) => r.full_name.toLowerCase().includes(q));
  }, [repos, repoFilter]);

  const missingInputs = useMemo(() => {
    if (!selectedPattern) return [] as string[];
    return selectedPattern.inputs
      .filter((i) => i.required && !(inputs[i.name] ?? "").trim())
      .map((i) => i.name);
  }, [selectedPattern, inputs]);

  const canSubmit =
    state.mode !== "saving" &&
    !!selectedPattern &&
    selectedRepoIds.size > 0 &&
    missingInputs.length === 0;

  if (repos.length === 0) {
    return (
      <Card>
        <CardHeader
          title="Activate a repo first"
          subtitle="Fleet requests fan out across activated repos — finish onboarding to enable the form."
        />
      </Card>
    );
  }

  if (patterns.length === 0) {
    return (
      <Card>
        <CardHeader
          title="No request-mode patterns in the catalog"
          subtitle="Run `shipctl sync` or check your catalog configuration."
        />
      </Card>
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || !selectedPattern) return;
    setState({ mode: "saving" });
    try {
      const cleanedInputs: Record<string, string> = {};
      for (const [k, v] of Object.entries(inputs)) {
        const t = v.trim();
        if (t) cleanedInputs[k] = t;
      }
      const res = await fetch("/api/fleet/requests", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspaceId,
          pattern_id: selectedPattern.id,
          inputs: cleanedInputs,
          repo_ids: Array.from(selectedRepoIds),
          title: title.trim() || undefined,
        }),
      });
      const data = (await res.json()) as {
        fleet_request?: { id: string };
        error?: string;
        code?: string;
      };
      if (!res.ok || !data.fleet_request?.id) {
        setState({
          mode: "error",
          message: data.error || `HTTP ${res.status}`,
          code: data.code,
        });
        return;
      }
      router.push(
        `/fleet/requests/${encodeURIComponent(data.fleet_request.id)}`,
      );
    } catch (err) {
      setState({
        mode: "error",
        message: err instanceof Error ? err.message : "Unknown error",
      });
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        {/* Left — Pattern picker */}
        <div className="space-y-4 lg:col-span-3">
          <Card>
            <CardHeader
              title="1. Pick a pattern"
              subtitle="Same catalog the per-repo Requests page uses. Missing something? Have the AI draft it."
              action={
                <PatternAiAuthor
                  workspaceId={workspaceId}
                  defaultMode="request"
                  onPatternSaved={(saved) => {
                    // Splice the new row into our local catalog view
                    // in the ``source: "workspace"`` shape the picker
                    // already knows how to render.
                    const entry: ApiCatalogPattern = {
                      kind: "pattern",
                      id: saved.pattern_id,
                      name: saved.name,
                      version: null,
                      channel: null,
                      group: null,
                      tags: [],
                      description: saved.description,
                      content_sha256: null,
                      updated_at: saved.updated_at,
                      deprecated: false,
                      replaced_by: null,
                      yanked: false,
                      category: saved.category,
                      modes: saved.modes,
                      default_trigger: null,
                      lane_workflow: null,
                      resolved_lane_workflow: null,
                      include: [],
                      inputs: (saved.inputs ?? []) as unknown as ApiPatternInput[],
                      enabled_on_install: {},
                      source: "workspace",
                    };
                    setPatterns((prev) => [
                      entry,
                      ...prev.filter((p) => p.id !== entry.id),
                    ]);
                    setSelectedPatternId(entry.id);
                    setInputs({});
                  }}
                />
              }
            />
            <div className="space-y-5">
              {grouped.map((group) => (
                <section key={group.category}>
                  <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-widest text-white/55">
                    {group.category}
                  </h4>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {group.patterns.map((p) => (
                      <PatternChoice
                        key={p.id}
                        pattern={p}
                        selected={selectedPatternId === p.id}
                        onSelect={() => {
                          setSelectedPatternId(p.id);
                          setInputs({});
                        }}
                      />
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </Card>

          {selectedPattern ? (
            <Card>
              <CardHeader
                title={`2. Fill in ${selectedPattern.name ?? selectedPattern.id}`}
                subtitle={
                  selectedPattern.inputs.length === 0
                    ? "This pattern takes no inputs."
                    : "Same inputs will be sent to every selected repo."
                }
              />
              <div className="space-y-3">
                {selectedPattern.inputs.map((input) => (
                  <PatternInputField
                    key={input.name}
                    input={input}
                    value={inputs[input.name] ?? ""}
                    onChange={(v) =>
                      setInputs((prev) => ({ ...prev, [input.name]: v }))
                    }
                  />
                ))}
                <Field label="Title (optional)" hint="Label shown in the fleet requests list.">
                  <input
                    type="text"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder={`e.g. ${selectedPattern.name ?? selectedPattern.id} — Q2 sweep`}
                    className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 text-sm text-white focus:border-aqua focus:outline-none"
                  />
                </Field>
              </div>
            </Card>
          ) : null}
        </div>

        {/* Right — Repo multi-select */}
        <div className="lg:col-span-2">
          <Card>
            <CardHeader
              title="3. Select repos"
              subtitle={`${selectedRepoIds.size} of ${repos.length} selected`}
              action={
                <div className="flex gap-1.5">
                  <ButtonGhost
                    onClick={() =>
                      setSelectedRepoIds(new Set(filteredRepos.map((r) => r.id)))
                    }
                  >
                    All visible
                  </ButtonGhost>
                  <ButtonGhost onClick={() => setSelectedRepoIds(new Set())}>
                    Clear
                  </ButtonGhost>
                </div>
              }
            />
            <input
              type="search"
              value={repoFilter}
              onChange={(e) => setRepoFilter(e.target.value)}
              placeholder="Filter repos…"
              className="mb-3 w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 text-sm text-white focus:border-aqua focus:outline-none"
            />
            <ul className="max-h-[420px] space-y-1 overflow-y-auto pr-1">
              {filteredRepos.length === 0 ? (
                <li className="rounded-md border border-dashed border-white/10 bg-white/[0.02] px-3 py-2 text-[11px] text-white/55">
                  No repos match that filter.
                </li>
              ) : (
                filteredRepos.map((repo) => {
                  const checked = selectedRepoIds.has(repo.id);
                  return (
                    <li key={repo.id}>
                      <label
                        className={
                          "flex cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-xs transition " +
                          (checked
                            ? "border-aqua/50 bg-aqua/[0.06] text-white"
                            : "border-white/10 bg-white/[0.02] text-white/75 hover:border-white/25")
                        }
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() =>
                            setSelectedRepoIds((prev) => {
                              const next = new Set(prev);
                              if (next.has(repo.id)) next.delete(repo.id);
                              else next.add(repo.id);
                              return next;
                            })
                          }
                          className="h-3.5 w-3.5 accent-aqua"
                        />
                        <span className="truncate font-mono">
                          {repo.full_name}
                        </span>
                      </label>
                    </li>
                  );
                })
              )}
            </ul>
          </Card>
        </div>
      </div>

      {state.mode === "error" ? (
        <div className="rounded-md border border-coral/40 bg-coral/10 px-4 py-3 text-sm text-coral">
          {state.message}
          {state.code ? (
            <span className="ml-2 font-mono text-[11px] opacity-75">
              ({state.code})
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-3 border-t border-white/10 pt-5">
        <p className="text-xs text-white/55">
          {selectedPattern && selectedRepoIds.size > 0
            ? `Dispatching ${selectedPattern.name ?? selectedPattern.id} to ${selectedRepoIds.size} repo${selectedRepoIds.size === 1 ? "" : "s"}.`
            : "Pick a pattern and at least one repo to continue."}
          {missingInputs.length > 0 ? (
            <span className="ml-1 text-coral">
              Missing: {missingInputs.join(", ")}.
            </span>
          ) : null}
        </p>
        <ButtonPrimary type="submit">
          {state.mode === "saving" ? "Dispatching…" : "Dispatch fleet request"}
        </ButtonPrimary>
      </div>
    </form>
  );
}

function PatternChoice({
  pattern,
  selected,
  onSelect,
}: {
  pattern: ApiCatalogPattern;
  selected: boolean;
  onSelect: () => void;
}) {
  const tags = [pattern.category, ...pattern.tags.slice(0, 1)].filter(
    (v): v is string => !!v,
  );
  return (
    <button
      type="button"
      onClick={onSelect}
      className={
        "text-left rounded-lg border p-3 transition " +
        (selected
          ? "border-aqua/50 bg-aqua/[0.06]"
          : "border-white/10 bg-white/[0.02] hover:border-white/25")
      }
    >
      <p className="truncate text-sm font-semibold text-white">
        {pattern.name ?? pattern.id}
      </p>
      <p className="mt-0.5 line-clamp-2 text-[11px] text-white/55">
        {pattern.description || pattern.id}
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        {pattern.source === "workspace" ? (
          <Badge tone="workspace">custom</Badge>
        ) : null}
        {tags.map((t) => (
          <Badge key={t} tone="neutral">
            {t}
          </Badge>
        ))}
        <span className="font-mono text-[10px] text-white/40">{pattern.id}</span>
      </div>
    </button>
  );
}

function PatternInputField({
  input,
  value,
  onChange,
}: {
  input: ApiPatternInput;
  value: string;
  onChange: (next: string) => void;
}) {
  const kind = (input.type ?? "text").toLowerCase();
  const label = input.name + (input.required ? " *" : "");

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
