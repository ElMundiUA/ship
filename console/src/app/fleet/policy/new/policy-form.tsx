"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { PatternAiAuthor } from "@/components/pattern-ai-author";
import { Badge, ButtonGhost, ButtonPrimary, Card, CardHeader } from "@/components/ui";
import type { ApiCatalogPattern, ApiPatternInput } from "@/lib/api/client";

/**
 * Client-side "New policy" form.
 *
 * The form is a *thin* wrapper over ``POST /api/policies`` — we
 * don't validate pattern inputs here because mirror-lane policies
 * apply the inputs at lane-materialisation time per repo (and the
 * backend does the coerce/required check then). Cadence is a free
 * string today; validation lives where the wizard writes workflows.
 */
export function NewPolicyForm({
  workspaceId,
  patterns: initialPatterns,
}: {
  workspaceId: string;
  patterns: ApiCatalogPattern[];
}) {
  const router = useRouter();

  // Local copy so a freshly-authored pattern (RFC-0008 §H / PR-6 AI
  // author modal) lands in the picker without a full-page reload.
  const [patterns, setPatterns] = useState<ApiCatalogPattern[]>(initialPatterns);
  const [selectedPatternId, setSelectedPatternId] = useState<string | null>(
    patterns[0]?.id ?? null,
  );
  const [name, setName] = useState("");
  const [laneId, setLaneId] = useState("");
  const [cadence, setCadence] = useState("@daily");
  const [agentSlug, setAgentSlug] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const grouped = useMemo(() => groupByCategory(patterns), [patterns]);
  const selected = patterns.find((p) => p.id === selectedPatternId) ?? null;

  function pickPattern(id: string) {
    setSelectedPatternId(id);
    // Seed sensible defaults the user can still override.
    if (!name) {
      const pat = patterns.find((p) => p.id === id);
      if (pat?.description) setName(pat.description.slice(0, 120));
    }
    if (!laneId) {
      const slug = id.replace(/^[^-]+-/, "").replace(/-/g, "_");
      setLaneId(slug);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (!selectedPatternId) {
      setError("Pick a pattern first.");
      return;
    }
    if (!name.trim()) {
      setError("Give the policy a name.");
      return;
    }
    if (!laneId.trim()) {
      setError("Lane id is required — this is the slug used in .ship/config.yml.");
      return;
    }
    if (!cadence.trim()) {
      setError("Cadence is required (e.g. @daily or a cron expression).");
      return;
    }

    setSubmitting(true);
    try {
      const res = await fetch("/api/policies", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspaceId,
          name: name.trim(),
          pattern_id: selectedPatternId,
          lane_id: laneId.trim(),
          cadence: cadence.trim(),
          agent_slug: agentSlug.trim() || null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? `HTTP ${res.status}`);
      }
      router.push("/fleet/policy");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create policy");
    } finally {
      setSubmitting(false);
    }
  }

  // Always provide the AI author affordance — even when the catalog
  // is empty the operator can author their first pattern here.
  const aiAuthor = (
    <PatternAiAuthor
      workspaceId={workspaceId}
      defaultMode="lane"
      onPatternSaved={(saved) => {
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
        pickPattern(entry.id);
      }}
    />
  );

  if (patterns.length === 0) {
    return (
      <Card>
        <CardHeader
          title="No lane-capable patterns in catalog"
          subtitle="Only patterns advertising 'lane' mode can back a mirror policy. Author one with AI to get started."
          action={aiAuthor}
        />
      </Card>
    );
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Card>
        <CardHeader
          title="Pattern"
          subtitle="This is the pattern every repo will run as a scheduled lane. Missing something? Author a custom one."
          action={aiAuthor}
        />
        <div className="mt-3 max-h-72 overflow-auto rounded-md border border-white/10 bg-black/20 p-2">
          {Object.entries(grouped).map(([category, list]) => (
            <div key={category} className="mb-3 last:mb-0">
              <div className="px-1 pb-1 text-[10px] font-bold uppercase tracking-[0.18em] text-white/40">
                {category}
              </div>
              <ul className="flex flex-col gap-1">
                {list.map((p) => {
                  const isSelected = p.id === selectedPatternId;
                  return (
                    <li key={p.id}>
                      <button
                        type="button"
                        onClick={() => pickPattern(p.id)}
                        className={`w-full rounded-sm border px-3 py-2 text-left text-xs transition ${
                          isSelected
                            ? "border-aqua/70 bg-aqua/10 text-white"
                            : "border-transparent text-white/80 hover:border-white/15 hover:bg-white/5"
                        }`}
                      >
                        <div className="flex items-center gap-1.5 font-mono">
                          <span>{p.id}</span>
                          {p.source === "workspace" ? (
                            <Badge tone="workspace">custom</Badge>
                          ) : null}
                        </div>
                        {p.description ? (
                          <div className="mt-0.5 text-[11px] text-white/55">
                            {p.description}
                          </div>
                        ) : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Rule"
          subtitle={
            selected
              ? `Selected: ${selected.id}`
              : "Pick a pattern to see its details."
          }
        />
        <div className="mt-3 flex flex-col gap-3">
          <Labeled label="Policy name" hint="Shown in the list — e.g. 'Nightly retro'">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={120}
              className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 text-sm text-white focus:border-aqua focus:outline-none"
            />
          </Labeled>
          <Labeled
            label="Lane id"
            hint="Slug used in .ship/config.yml — unique per workspace."
          >
            <input
              value={laneId}
              onChange={(e) => setLaneId(e.target.value)}
              maxLength={64}
              className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 font-mono text-sm text-white focus:border-aqua focus:outline-none"
            />
          </Labeled>
          <Labeled
            label="Cadence"
            hint="@daily, @weekly, or a cron expression — validated when the wizard wires it."
          >
            <input
              value={cadence}
              onChange={(e) => setCadence(e.target.value)}
              maxLength={120}
              className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 font-mono text-sm text-white focus:border-aqua focus:outline-none"
            />
          </Labeled>
          <Labeled
            label="Agent override (optional)"
            hint="Pattern default is used when blank."
          >
            <input
              value={agentSlug}
              onChange={(e) => setAgentSlug(e.target.value)}
              maxLength={64}
              placeholder="custom"
              className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 font-mono text-sm text-white focus:border-aqua focus:outline-none"
            />
          </Labeled>
        </div>
      </Card>

      {error ? (
        <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-100">
          {error}
        </div>
      ) : null}

      <div className="flex items-center gap-2">
        <ButtonPrimary type="submit">
          {submitting ? "Creating…" : "Create policy"}
        </ButtonPrimary>
        <ButtonGhost
          type="button"
          onClick={() => router.push("/fleet/policy")}
        >
          Cancel
        </ButtonGhost>
      </div>
    </form>
  );
}

function Labeled({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-white/60">
        {label}
      </span>
      {children}
      {hint ? <span className="text-[11px] text-white/45">{hint}</span> : null}
    </label>
  );
}

function groupByCategory(
  patterns: ApiCatalogPattern[],
): Record<string, ApiCatalogPattern[]> {
  const out: Record<string, ApiCatalogPattern[]> = {};
  for (const p of patterns) {
    const cat = p.category ?? "uncategorised";
    (out[cat] ??= []).push(p);
  }
  for (const list of Object.values(out)) {
    list.sort((a, b) => a.id.localeCompare(b.id));
  }
  return out;
}
