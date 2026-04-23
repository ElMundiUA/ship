"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ButtonGhost, ButtonPrimary, Card, CardHeader } from "@/components/ui";

/**
 * Client-side "New policy" form.
 *
 * Thin wrapper over ``POST /api/policies``. Title doubles as the
 * H2 heading in the rendered preamble; body is markdown injected
 * verbatim into the agent system prompt. ``sort_order`` controls
 * the order policies appear in the preamble — lower first.
 */
export function NewPolicyForm({ workspaceId }: { workspaceId: string }) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [sortOrder, setSortOrder] = useState(0);
  const [enabled, setEnabled] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (!title.trim()) {
      setError("Title is required.");
      return;
    }
    if (!body.trim()) {
      setError("Body is required.");
      return;
    }

    setSubmitting(true);
    try {
      const res = await fetch("/api/policies", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspaceId,
          title: title.trim(),
          body,
          enabled,
          sort_order: sortOrder,
        }),
      });
      if (!res.ok) {
        const eb = await res.json().catch(() => ({}));
        throw new Error(eb?.error ?? `HTTP ${res.status}`);
      }
      router.push("/settings/policy");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create");
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex max-w-3xl flex-col gap-4">
      <Card>
        <CardHeader
          title="Standing rule"
          subtitle="Title becomes a markdown heading in the agent's system prompt; body is injected verbatim."
        />
        <div className="flex flex-col gap-4">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
              Title
            </span>
            <input
              type="text"
              value={title}
              maxLength={160}
              placeholder="Always work via PR"
              onChange={(e) => setTitle(e.target.value)}
              className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/50"
              required
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
              Body (markdown)
            </span>
            <textarea
              value={body}
              rows={10}
              placeholder={
                "Never push directly to main. Open a pull request and wait for CI to go green before merging."
              }
              onChange={(e) => setBody(e.target.value)}
              className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 font-mono text-xs leading-relaxed text-white outline-none focus:border-aqua/50"
              required
            />
          </label>
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-xs text-white/75">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                className="h-4 w-4 rounded border-white/20 bg-white/[0.04] accent-aqua"
              />
              Enabled
            </label>
            <label className="flex items-center gap-2 text-xs text-white/75">
              Sort order
              <input
                type="number"
                value={sortOrder}
                onChange={(e) =>
                  setSortOrder(Number.parseInt(e.target.value, 10) || 0)
                }
                className="w-20 rounded-lg border border-white/10 bg-white/[0.04] px-2 py-1 text-sm text-white outline-none focus:border-aqua/50"
              />
            </label>
          </div>
        </div>
      </Card>

      {error ? (
        <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 px-4 py-2 text-xs text-rose-200">
          {error}
        </div>
      ) : null}

      <div className="flex justify-end gap-2">
        <ButtonGhost
          onClick={() => router.push("/settings/policy")}
          type="button"
        >
          Cancel
        </ButtonGhost>
        <ButtonPrimary type="submit">
          {submitting ? "Creating…" : "Create policy"}
        </ButtonPrimary>
      </div>
    </form>
  );
}
