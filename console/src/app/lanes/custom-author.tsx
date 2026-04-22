"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge, Card, CardHeader } from "@/components/ui";
import type { ApiActivatedRepo, ApiRepoConfig } from "@/lib/api/client";

/**
 * Custom-lane author — the ``?tab=new`` surface (Phase 3).
 *
 * What the operator gives us:
 *
 * - ``lane_id`` — slug that becomes the ``lanes:`` key, the workflow
 *   filename (``ship-<slug>.yml``) and the prompt file
 *   (``.ship/lanes/<slug>.md``).
 * - ``agent_slug`` — informational; surfaced to the reviewer in the
 *   PR body. The custom-lane workflow template isn't agent-aware
 *   (yet); swapping agents happens in ``.ship/lanes/<slug>.md`` or
 *   by editing the generated workflow after the PR lands.
 * - ``schedule`` — cron. MVP only supports scheduled lanes; pattern/
 *   event authoring lands in a follow-up.
 * - ``prompt`` — free-form markdown, ends up as the prompt file.
 *
 * The ``base_sha`` is read server-side from ``ApiRepoConfig.sha`` and
 * threaded through so the backend can optimistic-lock on the current
 * ``.ship/config.yml`` SHA. If the repo has no config yet we pass
 * ``null``, which the server accepts as "create the file fresh".
 */

const AGENT_CHOICES: { slug: string; label: string; hint: string }[] = [
  { slug: "claude", label: "Claude", hint: "Anthropic — general reasoning." },
  { slug: "gpt", label: "GPT", hint: "OpenAI — general reasoning." },
  { slug: "gemini", label: "Gemini", hint: "Google — code + long context." },
  {
    slug: "custom",
    label: "Custom",
    hint: "Wire your own binary via the workflow YAML after merge.",
  },
];

const SCHEDULE_PRESETS: { label: string; value: string; hint: string }[] = [
  {
    label: "Daily at 06:00",
    value: "0 6 * * *",
    hint: "Every day, once — good for digests.",
  },
  {
    label: "Weekdays at 09:00",
    value: "0 9 * * 1-5",
    hint: "Mon–Fri morning — standups, triage.",
  },
  {
    label: "Weekly — Monday at 06:00",
    value: "0 6 * * 1",
    hint: "Weekly cadence — retro, tech-debt sweep.",
  },
  {
    label: "Nightly at 04:00",
    value: "0 4 * * *",
    hint: "Off-hours — self-heal, housekeeping.",
  },
];

export function CustomLaneAuthor({
  workspaceId,
  selectedRepo,
  repos,
  config,
}: {
  workspaceId: string;
  selectedRepo: ApiActivatedRepo | null;
  repos: ApiActivatedRepo[];
  config: ApiRepoConfig | null;
}) {
  const [laneId, setLaneId] = useState("");
  const [agentSlug, setAgentSlug] = useState(AGENT_CHOICES[0].slug);
  const [schedule, setSchedule] = useState(SCHEDULE_PRESETS[1].value);
  const [prompt, setPrompt] = useState("");
  const [summary, setSummary] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const existingLanes = useMemo<Set<string>>(() => {
    const parsed = config?.parsed?.lanes;
    if (!parsed || typeof parsed !== "object") return new Set();
    return new Set(Object.keys(parsed));
  }, [config]);

  const slugError = useMemo(() => {
    if (!laneId) return null;
    if (!/^[a-z][a-z0-9_-]{0,62}$/.test(laneId)) {
      return "Use lowercase letters, digits, `_` or `-` (start with a letter).";
    }
    if (existingLanes.has(laneId)) {
      return "A lane with this id already exists in .ship/config.yml.";
    }
    return null;
  }, [laneId, existingLanes]);

  const canSave =
    !!selectedRepo &&
    !!laneId &&
    !slugError &&
    prompt.trim().length > 0 &&
    schedule.trim().length > 0 &&
    !saving;

  if (!selectedRepo) {
    return (
      <Card>
        <CardHeader
          title="Activate a repo first"
          subtitle="Custom lanes write to .ship/config.yml + .github/workflows on a specific repo. Activate one via onboarding to enable this tab."
        />
        <div className="mt-4">
          <Link
            href="/onboarding?step=github"
            className="inline-flex rounded border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua hover:bg-aqua/20"
          >
            Open onboarding →
          </Link>
        </div>
      </Card>
    );
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedRepo) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch("/api/lanes/custom-propose", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspaceId,
          repoId: selectedRepo.id,
          lane_id: laneId,
          agent_slug: agentSlug,
          schedule,
          prompt,
          base_sha: config?.sha ?? null,
          change_summary: summary,
        }),
      });
      const data = (await res.json()) as {
        pr_url?: string;
        error?: string;
        code?: string;
      };
      if (!res.ok || !data.pr_url) {
        setError(data.error || `HTTP ${res.status}`);
        return;
      }
      // Ship back to GitHub so the reviewer can land the PR.
      window.location.href = data.pr_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="space-y-5" onSubmit={handleSave}>
      {repos.length > 1 ? (
        <div className="flex flex-wrap items-center gap-2 text-xs text-white/55">
          <span className="font-semibold">Repo:</span>
          {repos.map((r) => (
            <Link
              key={r.id}
              href={`/lanes?tab=new&repo_id=${encodeURIComponent(r.id)}`}
              className={
                "rounded-full border px-2.5 py-1 font-mono text-[11px] transition " +
                (r.id === selectedRepo.id
                  ? "border-aqua/40 bg-aqua/10 text-aqua"
                  : "border-white/10 bg-white/[0.04] text-white/70 hover:text-white")
              }
            >
              {r.full_name}
            </Link>
          ))}
        </div>
      ) : null}

      <Card>
        <CardHeader
          title="Author a custom lane"
          subtitle="Ship will open a single PR that adds the workflow YAML, the prompt file, and the lane entry in .ship/config.yml."
        />

        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field
            label="Lane id (slug)"
            hint="Used as the key under lanes:, the workflow filename, and the prompt path."
            error={slugError}
          >
            <input
              type="text"
              value={laneId}
              onChange={(e) => setLaneId(e.target.value.toLowerCase())}
              placeholder="weekly-tech-debt"
              autoComplete="off"
              className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 font-mono text-sm text-white focus:border-aqua focus:outline-none"
            />
          </Field>

          <Field label="Agent" hint="Which agent the workflow invokes.">
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
            <p className="mt-1 text-[11px] text-white/45">
              {AGENT_CHOICES.find((a) => a.slug === agentSlug)?.hint}
            </p>
          </Field>
        </div>

        <div className="mt-4">
          <Field
            label="Schedule (cron)"
            hint="Cron expression evaluated in UTC. Pick a preset or type your own."
          >
            <div className="flex flex-wrap items-center gap-2">
              {SCHEDULE_PRESETS.map((p) => (
                <button
                  key={p.value}
                  type="button"
                  onClick={() => setSchedule(p.value)}
                  className={
                    "rounded-full border px-2.5 py-1 text-[11px] font-semibold transition " +
                    (schedule === p.value
                      ? "border-aqua/40 bg-aqua/10 text-aqua"
                      : "border-white/10 bg-white/[0.04] text-white/70 hover:text-white")
                  }
                  title={p.hint}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <input
              type="text"
              value={schedule}
              onChange={(e) => setSchedule(e.target.value)}
              placeholder="0 6 * * *"
              autoComplete="off"
              className="mt-2 w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 font-mono text-sm text-white focus:border-aqua focus:outline-none"
            />
          </Field>
        </div>

        <div className="mt-4">
          <Field
            label="Prompt"
            hint="Ends up at .ship/lanes/<slug>.md — free-form markdown. Describe inputs, expected output, and guardrails."
          >
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={8}
              placeholder="You are an on-call SRE agent. Every day at 04:00 UTC, scan open incidents and draft a summary to post in #ops."
              className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-2 font-mono text-[13px] leading-relaxed text-white focus:border-aqua focus:outline-none"
            />
          </Field>
        </div>

        <div className="mt-4">
          <Field
            label="PR description (optional)"
            hint="Added to the PR body so reviewers have context."
          >
            <textarea
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              rows={2}
              placeholder="Adds a nightly tech-debt sweep for the platform team."
              className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-2 text-sm text-white focus:border-aqua focus:outline-none"
            />
          </Field>
        </div>

        {error ? (
          <div className="mt-4 rounded-md border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
            {error}
          </div>
        ) : null}

        <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
          <p className="text-[11px] text-white/55">
            Base SHA:{" "}
            <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
              {config?.sha ? config.sha.slice(0, 7) : "new file"}
            </code>{" "}
            <Badge tone="neutral">
              {existingLanes.size} lane{existingLanes.size === 1 ? "" : "s"}{" "}
              already defined
            </Badge>
          </p>
          <div className="flex items-center gap-2">
            <Link
              href="/lanes"
              className="rounded-md border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 hover:border-white/30 hover:text-white"
            >
              Cancel
            </Link>
            <button
              type="submit"
              disabled={!canSave}
              className={
                "rounded-md border px-4 py-1.5 text-xs font-semibold transition " +
                (canSave
                  ? "border-aqua/50 bg-aqua/15 text-aqua hover:bg-aqua/25"
                  : "cursor-not-allowed border-white/15 bg-white/[0.04] text-white/45")
              }
            >
              {saving ? "Opening PR…" : "Open PR →"}
            </button>
          </div>
        </div>
      </Card>
    </form>
  );
}

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-[10px] font-semibold uppercase tracking-widest text-white/55">
        {label}
      </label>
      <div className="mt-1">{children}</div>
      {error ? (
        <p className="mt-1 text-[11px] text-coral">{error}</p>
      ) : hint ? (
        <p className="mt-1 text-[11px] text-white/45">{hint}</p>
      ) : null}
    </div>
  );
}
