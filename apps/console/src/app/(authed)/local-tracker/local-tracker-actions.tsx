"use client";

/**
 * Action islands for the /local-tracker page (E19 step 5).
 *
 * All three controls hit a Next.js route handler that re-issues the
 * call against the backend server-side — the same pattern as
 * `/memory` page actions. Keeps the session token out of the
 * browser bundle.
 */

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";


function buttonClasses(variant: "default" | "destructive" = "default") {
  if (variant === "destructive") {
    return "shrink-0 px-2 py-1 text-xs rounded border border-destructive text-destructive hover:bg-destructive hover:text-destructive-foreground disabled:opacity-40 disabled:cursor-not-allowed";
  }
  return "shrink-0 px-2 py-1 text-xs rounded border border-border hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed";
}


export function StageBumpControl({
  workspaceId,
  displayId,
  currentStage,
  stages,
}: {
  workspaceId: string;
  displayId: string;
  currentStage: string | null;
  stages: string[];
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [target, setTarget] = useState(currentStage ?? stages[0]);

  function onClick() {
    if (target === currentStage) return;
    startTransition(async () => {
      const resp = await fetch("/api/local-tracker/transition", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ workspaceId, displayId, toState: target }),
      });
      if (resp.ok) router.refresh();
    });
  }

  return (
    <div className="flex items-center gap-1 text-xs">
      <select
        value={target}
        onChange={(e) => setTarget(e.target.value)}
        className="bg-background border border-border rounded px-1 py-0.5"
      >
        {stages.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={onClick}
        disabled={pending || target === currentStage}
        className={buttonClasses()}
      >
        {pending ? "…" : "Bump"}
      </button>
    </div>
  );
}


export function PrMergeButton({
  workspaceId,
  owner,
  name,
  number,
}: {
  workspaceId: string;
  owner: string;
  name: string;
  number: number;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function onClick() {
    startTransition(async () => {
      const resp = await fetch("/api/local-tracker/merge-pr", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ workspaceId, owner, name, number }),
      });
      if (resp.ok) router.refresh();
    });
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={pending}
      className={buttonClasses()}
    >
      {pending ? "…" : "Merge"}
    </button>
  );
}


export function CiRerunButton({
  workspaceId,
  owner,
  name,
  runId,
}: {
  workspaceId: string;
  owner: string;
  name: string;
  runId: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function onClick() {
    startTransition(async () => {
      const resp = await fetch("/api/local-tracker/rerun-ci", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ workspaceId, owner, name, runId }),
      });
      if (resp.ok) router.refresh();
    });
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={pending}
      className={buttonClasses()}
    >
      {pending ? "…" : "Rerun"}
    </button>
  );
}
