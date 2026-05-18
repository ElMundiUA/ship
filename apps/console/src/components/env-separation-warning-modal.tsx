"use client";

import { useCallback, useEffect, useId, useState } from "react";

import { ButtonPrimary } from "@/components/ui";

type Warning = {
  handle: string;
  project_id: string;
  project_name: string;
};

type Props = {
  workspaceId: string;
};

/**
 * One-time modal when agents first dispatch on a project (ELS-144).
 */
export function EnvSeparationWarningModal({ workspaceId }: Props) {
  const [pending, setPending] = useState<Warning | null>(null);
  const [acking, setAcking] = useState(false);
  const titleId = useId();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `/api/env-separation-warnings?ws=${encodeURIComponent(workspaceId)}`,
          { cache: "no-store" },
        );
        if (!res.ok) return;
        const rows = (await res.json()) as Warning[];
        if (!cancelled && rows.length > 0) {
          setPending(rows[0]);
        }
      } catch {
        // Best-effort.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  const dismiss = useCallback(async () => {
    if (!pending || acking) return;
    setAcking(true);
    try {
      await fetch("/api/env-separation-warnings/ack", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ws: workspaceId, handle: pending.handle }),
      });
    } finally {
      setPending(null);
      setAcking(false);
    }
  }, [acking, pending, workspaceId]);

  if (!pending) return null;

  const name = pending.project_name || pending.project_id;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-end justify-center bg-black/70 p-4 backdrop-blur-sm sm:items-center"
      role="presentation"
    >
      <div
        className="w-full max-w-lg rounded-2xl border border-white/10 bg-ink/95 p-6 shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-aqua/85">
          Before unattended dispatch
        </p>
        <h2 id={titleId} className="mt-2 font-display text-xl font-bold text-white">
          Agents will auto-merge in &ldquo;{name}&rdquo;
        </h2>
        <div className="mt-4 space-y-2 text-sm leading-relaxed text-white/75">
          <p>
            Ship just started dispatching agent work on a ticket in this project.
            Tickets run one-at-a-time and the auto-merger can ship PRs when the
            reviewer is confident.
          </p>
          <ul className="list-disc space-y-1 pl-5 text-white/65">
            <li>Use a non-prod default branch with deploy gates downstream.</li>
            <li>
              Point CI secrets at dev/staging DB, API, and payment credentials.
            </li>
            <li>
              Keep CODEOWNERS on risky paths so auto-merge cannot skip required
              review.
            </li>
          </ul>
        </div>
        <div className="mt-6 flex justify-end">
          <ButtonPrimary type="button" disabled={acking} onClick={() => void dismiss()}>
            {acking ? "Saving…" : "Acknowledge"}
          </ButtonPrimary>
        </div>
      </div>
    </div>
  );
}
