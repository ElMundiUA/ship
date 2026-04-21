"use client";

/**
 * Phase 7b — connector bucket detail card.
 *
 * Shows the integration handle the bucket mirrors and gives the
 * operator a one-click "Sync now" that POSTs to the backend's
 * ``/sync`` endpoint. The stub adapter on the backend synthesizes
 * a deterministic markdown page from the stored ``source_ref``, so
 * even before real connector fetchers exist the upstream flow
 * ("connect → sync → article appears in the bucket") is observable
 * in the UI.
 *
 * On success the server action also ``revalidatePath``s the bucket
 * page so the Runs card + Articles card redraw with the fresh
 * DistillerRun without a full reload.
 */

import { useState, useTransition } from "react";

import { Badge, Card, CardHeader } from "@/components/ui";

import { syncConnectorBucketAction, type SyncActionResult } from "./actions";

export function ConnectorCard({
  workspaceId,
  slug,
  integrationKind,
  resourceRef,
}: {
  workspaceId: string;
  slug: string;
  integrationKind: string;
  resourceRef: Record<string, unknown>;
}) {
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<SyncActionResult | null>(null);

  function onClick() {
    startTransition(async () => {
      setResult(null);
      const res = await syncConnectorBucketAction(workspaceId, slug);
      setResult(res);
    });
  }

  const refEntries = Object.entries(resourceRef);

  return (
    <Card data-testid="bucket-connector-card">
      <CardHeader
        title="Connector"
        subtitle="Live index of the configured third-party resource."
      />

      <dl className="mb-3 space-y-1 text-xs text-white/70">
        <div className="flex items-center gap-2">
          <dt className="w-20 font-semibold uppercase tracking-widest text-white/45">
            Provider
          </dt>
          <dd>
            <Badge tone="neutral">{integrationKind}</Badge>
          </dd>
        </div>
        {refEntries.length > 0 ? (
          refEntries.map(([k, v]) => (
            <div key={k} className="flex items-start gap-2">
              <dt className="w-20 font-semibold uppercase tracking-widest text-white/45">
                {k}
              </dt>
              <dd className="font-mono text-[11px] text-aqua/90 break-all">
                {typeof v === "string" ? v : JSON.stringify(v)}
              </dd>
            </div>
          ))
        ) : (
          <div className="text-[10px] text-white/45">
            No resource_ref captured.
          </div>
        )}
      </dl>

      <button
        type="button"
        data-testid="bucket-connector-sync"
        onClick={onClick}
        disabled={pending}
        className="inline-flex w-full items-center justify-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3.5 py-1.5 text-xs font-bold text-ink shadow-glow transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {pending ? "Syncing…" : "Sync now"}
      </button>

      {result !== null && (
        <div
          data-testid="bucket-connector-sync-result"
          data-ok={String(result.ok)}
          className={`mt-3 rounded-lg border px-3 py-2 text-xs ${
            result.ok
              ? "border-aqua/40 bg-aqua/10 text-aqua/95"
              : "border-coral/40 bg-coral/10 text-coral/95"
          }`}
        >
          {result.ok ? (
            <>
              <Badge tone={result.decision === "skip" ? "neutral" : "ok"}>
                {result.decision}
              </Badge>
              <p className="mt-1 text-white/85">{result.message}</p>
            </>
          ) : (
            <>
              <div className="font-semibold">
                Sync failed{result.status ? ` (${result.status})` : ""}
              </div>
              <p className="mt-1 text-white/85">{result.message}</p>
            </>
          )}
        </div>
      )}
    </Card>
  );
}
