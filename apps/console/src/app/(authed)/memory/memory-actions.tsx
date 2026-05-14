"use client";

/**
 * Memory-page action islands (E17/ELS-130).
 *
 * Two interactive controls because the rest of the page is a
 * Server Component:
 *
 * - ``MemoryRowDelete`` — per-fact delete with arm/confirm flow.
 * - ``MemoryActions`` — bulk "forget last N days" form.
 *
 * Both trigger ``router.refresh()`` on success so the
 * server-rendered list re-fetches without a full page reload.
 */

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import {
  bulkForgetNavigatorMemories,
  deleteNavigatorMemory,
} from "@/lib/api/client";


export function MemoryActions({
  workspaceId,
}: {
  workspaceId: string;
}) {
  const router = useRouter();
  const [days, setDays] = useState(7);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!confirmed) return;
    setError(null);
    startTransition(async () => {
      try {
        const r = await bulkForgetNavigatorMemories(workspaceId, days);
        setConfirmed(false);
        router.refresh();
        if (r.deleted === 0) setError("Nothing to forget in that window.");
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to forget memories.",
        );
      }
    });
  }

  return (
    <form
      onSubmit={onSubmit}
      className="flex flex-wrap items-end gap-3 p-3 border border-border rounded-lg bg-muted/30"
    >
      <label className="flex flex-col gap-1 text-xs">
        <span className="font-medium text-foreground">
          Forget the last
        </span>
        <select
          value={days}
          onChange={(e) => setDays(parseInt(e.target.value, 10))}
          className="bg-background border border-border rounded px-2 py-1 text-sm"
        >
          {[1, 3, 7, 14, 30, 60, 90].map((d) => (
            <option key={d} value={d}>
              {d} day{d === 1 ? "" : "s"}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
        />
        I understand this is permanent
      </label>
      <button
        type="submit"
        disabled={!confirmed || pending}
        className="px-3 py-1 text-sm rounded border border-destructive text-destructive hover:bg-destructive hover:text-destructive-foreground disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {pending ? "Forgetting…" : "Forget"}
      </button>
      {error && (
        <span className="text-xs text-destructive">{error}</span>
      )}
    </form>
  );
}


export function MemoryRowDelete({
  workspaceId,
  memoryId,
}: {
  workspaceId: string;
  memoryId: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [armed, setArmed] = useState(false);

  function onClick() {
    if (!armed) {
      setArmed(true);
      // Re-arm after 4 seconds so an accidental hover doesn't leave
      // a destructive button hot indefinitely.
      window.setTimeout(() => setArmed(false), 4000);
      return;
    }
    startTransition(async () => {
      try {
        await deleteNavigatorMemory(workspaceId, memoryId);
        router.refresh();
      } catch {
        setArmed(false);
      }
    });
  }

  return (
    <button
      type="button"
      disabled={pending}
      onClick={onClick}
      className={
        armed
          ? "shrink-0 px-2 py-1 text-xs rounded bg-destructive text-destructive-foreground"
          : "shrink-0 px-2 py-1 text-xs rounded text-muted-foreground hover:text-foreground"
      }
    >
      {pending ? "…" : armed ? "Confirm" : "Forget"}
    </button>
  );
}
