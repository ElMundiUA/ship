"use client";

/**
 * Agent provider keys — settings panel (Agents & access tab).
 *
 * Lets an operator add OR rotate the per-provider agent API keys (Claude /
 * Cursor / Codex, plus optional LLM-fallback keys) for a chosen repository.
 * Keys are GitHub Actions secrets stored per-repo, so the panel is scoped by a
 * repo selector. Reuses the same BFF routes as the onboarding wizard
 * (`/api/onboard/agent-secrets`), so there's one source of truth.
 *
 * Unlike the onboarding card, the paste input is shown even when a key is
 * already `present` — rotation is a first-class settings use-case and the
 * backend PUT overwrites idempotently.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, CardHeader } from "@/components/ui";
import type { ApiAgentSecretStatus } from "@/lib/api/client";
import type { ApiActivatedRepo } from "@/lib/api/types";

export function AgentSecretsPanel({
  workspaceId,
  repos,
}: {
  workspaceId: string;
  repos: ApiActivatedRepo[];
}) {
  const [repoId, setRepoId] = useState<string>(repos[0]?.id ?? "");
  const [agents, setAgents] = useState<ApiAgentSecretStatus[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [failed, setFailed] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const refresh = useCallback(
    async (rid: string) => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(
          `/api/onboard/agent-secrets?workspace_id=${encodeURIComponent(
            workspaceId,
          )}&repo_id=${encodeURIComponent(rid)}`,
        );
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as { check?: { agents?: ApiAgentSecretStatus[] } };
        setAgents(data?.check?.agents ?? []);
      } catch {
        setAgents([]);
        setError("Couldn't load agent secrets for this repository.");
      } finally {
        setLoading(false);
      }
    },
    [workspaceId],
  );

  useEffect(() => {
    if (repoId) refresh(repoId);
  }, [repoId, refresh]);

  if (repos.length === 0) {
    return (
      <Card>
        <CardHeader
          title="Agent provider keys"
          subtitle="Activate a repository first — agent keys are stored as GitHub Actions secrets on each repo."
        />
      </Card>
    );
  }

  async function save() {
    if (!repoId) return;
    const payload = Object.entries(drafts)
      .filter(([, v]) => v.trim().length > 0)
      .map(([slug, plaintext]) => ({ slug, plaintext: plaintext.trim() }));
    if (payload.length === 0) return;
    setSaving(true);
    setError(null);
    setFailed({});
    try {
      const res = await fetch("/api/onboard/agent-secrets", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ workspace_id: workspaceId, repo_id: repoId, secrets: payload }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        error?: string;
        result?: { pushed?: string[]; failed?: { slug: string; error?: string; reason?: string }[] };
      };
      if (!res.ok) {
        throw new Error(
          data?.error === "github_app_missing"
            ? "Ship's GitHub App lacks Secrets write access on this repo — reinstall/grant it, then retry."
            : data?.error ?? `Save failed (${res.status}).`,
        );
      }
      const result = data?.result;
      const nextFailed: Record<string, string> = {};
      for (const item of result?.failed ?? []) {
        nextFailed[item.slug] = item.error ?? item.reason ?? "failed";
      }
      setFailed(nextFailed);
      const pushed = new Set(result?.pushed ?? []);
      setDrafts((d) => {
        const next = { ...d };
        for (const slug of pushed) delete next[slug];
        return next;
      });
      setSavedAt(Date.now());
      await refresh(repoId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save secrets.");
    } finally {
      setSaving(false);
    }
  }

  // Coding-agent keys drive which CLIs can run; llm-* are optional fallbacks.
  // Hide an llm-* row whose secret_name a coding row already covers (dedupe).
  const coding = agents.filter((a) => !a.slug.startsWith("llm-"));
  const llm = agents.filter(
    (a) =>
      a.slug.startsWith("llm-") &&
      !coding.some((c) => c.secret_name && c.secret_name === a.secret_name),
  );
  const hasDrafts = Object.values(drafts).some((v) => v.trim().length > 0);

  return (
    <Card>
      <CardHeader
        title="Agent provider keys"
        subtitle="API keys for the coding agents Ship runs (Claude, Cursor, Codex). Stored as GitHub Actions secrets on the selected repo — set several so different process stages can use different backends. Add or paste a new value to rotate."
      />
      <div className="mt-4 space-y-4">
        {repos.length > 1 ? (
          <label className="block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
              Repository
            </span>
            <select
              value={repoId}
              onChange={(event) => {
                setRepoId(event.target.value);
                setDrafts({});
                setFailed({});
                setSavedAt(null);
              }}
              className="w-full rounded-md border border-white/10 bg-black/35 px-2 py-1.5 text-sm text-white outline-none transition focus:border-aqua/40 sm:max-w-md"
            >
              {repos.map((repo) => (
                <option key={repo.id} value={repo.id}>
                  {repo.full_name}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {error ? (
          <div className="rounded-md border border-coral/25 bg-coral/[0.05] px-2 py-1.5 text-[12px] text-coral/90">
            {error}
          </div>
        ) : null}

        {loading ? (
          <p className="text-sm text-white/45">Loading agent secrets…</p>
        ) : (
          <div className="space-y-2">
            {coding.map((a) => (
              <AgentSecretRow
                key={a.slug}
                secret={a}
                draft={drafts[a.slug] ?? ""}
                onDraft={(v) => setDrafts((d) => ({ ...d, [a.slug]: v }))}
                failure={failed[a.slug]}
              />
            ))}
          </div>
        )}

        {!loading && llm.length > 0 ? (
          <details className="rounded-xl border border-white/10 bg-white/[0.02] p-3">
            <summary className="cursor-pointer text-[10px] font-bold uppercase tracking-widest text-white/55">
              Optional LLM fallback keys
            </summary>
            <div className="mt-3 space-y-2">
              {llm.map((a) => (
                <AgentSecretRow
                  key={a.slug}
                  secret={a}
                  draft={drafts[a.slug] ?? ""}
                  onDraft={(v) => setDrafts((d) => ({ ...d, [a.slug]: v }))}
                  failure={failed[a.slug]}
                />
              ))}
            </div>
          </details>
        ) : null}

        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={!hasDrafts || saving}
            onClick={save}
            className="rounded-md border border-aqua/30 bg-aqua/[0.08] px-3 py-1.5 text-[12px] font-semibold text-aqua transition hover:bg-aqua/[0.14] disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.03] disabled:text-white/30"
          >
            {saving ? "Saving…" : "Save keys"}
          </button>
          {savedAt && !hasDrafts ? (
            <span className="text-[11px] text-white/45">Saved — secrets updated on the repo.</span>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

function AgentSecretRow({
  secret,
  draft,
  onDraft,
  failure,
}: {
  secret: ApiAgentSecretStatus;
  draft: string;
  onDraft: (value: string) => void;
  failure?: string;
}) {
  const pill = !secret.secret_name
    ? { text: "no key needed", cls: "text-white/55" }
    : secret.present
      ? { text: "present", cls: "text-aqua" }
      : secret.required
        ? { text: "missing", cls: "text-coral" }
        : { text: "optional", cls: "text-amber-200" };

  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-semibold text-white">{secret.label}</div>
        <span className={`text-[10px] font-bold uppercase tracking-widest ${pill.cls}`}>
          {pill.text}
        </span>
      </div>
      {secret.description ? (
        <p className="mt-0.5 text-[12px] leading-relaxed text-white/45">{secret.description}</p>
      ) : null}
      <div className="mt-1.5 flex items-center gap-2">
        {secret.secret_name ? (
          <code className="rounded bg-black/40 px-1.5 py-0.5 font-mono text-[11px] text-white/60">
            secrets.{secret.secret_name}
          </code>
        ) : null}
        {secret.vendor_url ? (
          <a
            href={secret.vendor_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] text-white/45 transition hover:text-aqua hover:underline"
          >
            Get key ↗
          </a>
        ) : null}
      </div>
      {/* Always render the input when a secret is expected — including when
          already present — so operators can rotate a key (backend overwrites
          idempotently). This is the fix vs the onboarding card, which hid it. */}
      {secret.secret_name ? (
        <input
          type="password"
          value={draft}
          onChange={(event) => onDraft(event.target.value)}
          placeholder={
            secret.present
              ? `${secret.secret_name} — paste a new key to replace`
              : `${secret.secret_name} — paste plaintext key`
          }
          className="mt-2 w-full rounded-md border border-white/10 bg-black/25 px-2 py-1.5 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-aqua/40"
        />
      ) : null}
      {failure ? (
        <span className="mt-1 block text-[10px] text-coral/80">Push failed: {failure}</span>
      ) : null}
    </div>
  );
}
