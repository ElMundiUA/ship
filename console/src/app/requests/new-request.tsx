"use client";

import { useState } from "react";

import { Card, CardHeader } from "@/components/ui";
import type { ApiActivatedRepo } from "@/lib/api/client";

/**
 * "New request" form — the one-shot dispatcher (Phase 3).
 *
 * Posts to ``/api/requests`` which proxies to
 * ``POST /v1/workspaces/{ws}/repos/{repo}/requests``. On success we
 * refresh the page so the freshly-dispatched row appears in the
 * "Recent requests" card. The GitHub Actions run URL lands on the
 * row once the workflow dispatch succeeds server-side.
 */

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

export function NewRequestForm({
  workspaceId,
  repos,
}: {
  workspaceId: string;
  repos: ApiActivatedRepo[];
}) {
  const [repoId, setRepoId] = useState<string>(repos[0]?.id ?? "");
  const [agentSlug, setAgentSlug] = useState<string>(AGENT_CHOICES[0].slug);
  const [contextRef, setContextRef] = useState<string>("");
  const [prompt, setPrompt] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit =
    !saving && !!repoId && !!agentSlug && prompt.trim().length > 0;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch("/api/requests", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspaceId,
          repoId,
          agent_slug: agentSlug,
          prompt,
          context_ref: contextRef || undefined,
        }),
      });
      const data = (await res.json()) as {
        id?: string;
        error?: string;
        code?: string;
      };
      if (!res.ok || !data.id) {
        setError(data.error || `HTTP ${res.status}`);
        return;
      }
      // Clear the prompt + reload so the new row shows up.
      setPrompt("");
      setContextRef("");
      // Soft refresh: App-router route refetch would be ideal here
      // (useRouter().refresh()) but we avoid the hook dance by just
      // reloading — the page is server-rendered and cheap.
      window.location.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSaving(false);
    }
  }

  if (repos.length === 0) {
    return (
      <Card>
        <CardHeader
          title="Activate a repo first"
          subtitle="Requests dispatch against a specific repo. Finish onboarding to enable the form."
        />
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="New request"
        subtitle="One-shot dispatch — the agent runs once and reports back to GitHub Actions."
      />
      <form className="mt-5 space-y-4" onSubmit={handleSubmit}>
        <Field label="Repo" hint="Request fires against this repo's default branch.">
          <select
            value={repoId}
            onChange={(e) => setRepoId(e.target.value)}
            className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 text-sm text-white focus:border-aqua focus:outline-none"
          >
            {repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.full_name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Agent" hint={AGENT_CHOICES.find((a) => a.slug === agentSlug)?.hint}>
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
          hint="Ticket URL, PR URL, file path — whatever grounds the run. Passed as ``inputs.context_ref``."
        >
          <input
            type="text"
            value={contextRef}
            onChange={(e) => setContextRef(e.target.value)}
            placeholder="https://linear.app/… or src/foo.py"
            className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 font-mono text-sm text-white focus:border-aqua focus:outline-none"
          />
        </Field>

        <Field
          label="Prompt"
          hint="What do you want the agent to deliver? Passed as ``inputs.prompt``."
        >
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={6}
            placeholder="Audit the PR for regressions in the payments flow and draft a review."
            className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-2 font-mono text-[13px] leading-relaxed text-white focus:border-aqua focus:outline-none"
          />
        </Field>

        {error ? (
          <div className="rounded-md border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
            {error}
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
            {saving ? "Dispatching…" : "Dispatch"}
          </button>
        </div>
      </form>
    </Card>
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
