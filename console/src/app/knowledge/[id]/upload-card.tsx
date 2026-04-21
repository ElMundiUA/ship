"use client";

import { useRef, useState, useTransition } from "react";

import { Badge, Card, CardHeader } from "@/components/ui";

import {
  type UploadActionResult,
  uploadBucketFileAction,
} from "./actions";

/**
 * Client-side upload card for the bucket detail page.
 *
 * Responsibilities:
 * - File picker (text/markdown only, enforced in the action).
 * - Classifier selector (auto / stub / llm) — power-user affordance
 *   so the demo can force the deterministic stub, and so operators
 *   can retry an LLM classifier after fixing a credential.
 * - Submit via the server action; surface the returned decision +
 *   reason inline so the user knows whether the file produced a new
 *   article, updated an existing one, or got de-duplicated.
 *
 * The state machine is intentionally small: `idle | pending | result`.
 * Next.js re-renders the surrounding server page via
 * ``revalidatePath`` after a successful upload, so we don't need to
 * maintain an in-memory list of articles here — the page below the
 * card already picks up new rows from its own server fetch.
 */
export function UploadCard({
  workspaceId,
  slug,
  bucketName,
}: {
  workspaceId: string;
  slug: string;
  bucketName: string;
}) {
  const formRef = useRef<HTMLFormElement | null>(null);
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<UploadActionResult | null>(null);
  const [filename, setFilename] = useState<string | null>(null);

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    startTransition(async () => {
      setResult(null);
      const res = await uploadBucketFileAction(workspaceId, slug, data);
      setResult(res);
      if (res.ok) {
        form.reset();
        setFilename(null);
      }
    });
  }

  return (
    <Card data-testid="bucket-upload-card">
      <CardHeader
        title="Upload article"
        subtitle={`Text or markdown file — routed through the Distiller into "${bucketName}"`}
      />
      <form ref={formRef} onSubmit={onSubmit} className="space-y-3">
        <label
          htmlFor="bucket-upload-file"
          className="block rounded-xl border border-dashed border-white/20 bg-white/[0.02] px-3 py-6 text-center text-xs text-white/55 hover:border-aqua/50 hover:bg-aqua/5"
        >
          <div className="font-semibold text-white/80">
            {filename ?? "Click to choose a file"}
          </div>
          <div className="mt-1 text-[10px] uppercase tracking-widest text-white/40">
            .md / .markdown / .txt · max 1 MB · utf-8
          </div>
          <input
            id="bucket-upload-file"
            name="file"
            type="file"
            accept=".md,.markdown,.txt,text/plain,text/markdown"
            className="sr-only"
            data-testid="bucket-upload-input"
            onChange={(event) => {
              const file = event.currentTarget.files?.[0] ?? null;
              setFilename(file ? file.name : null);
              setResult(null);
            }}
          />
        </label>

        <div className="flex items-center gap-2 text-[11px] text-white/55">
          <label htmlFor="bucket-upload-classifier" className="uppercase tracking-widest">
            Classifier
          </label>
          <select
            id="bucket-upload-classifier"
            name="classifier"
            defaultValue="auto"
            data-testid="bucket-upload-classifier"
            className="rounded border border-white/15 bg-black/30 px-2 py-1 font-mono text-[11px] text-white/85"
          >
            <option value="auto">auto (LLM, stub fallback)</option>
            <option value="stub">stub (deterministic)</option>
            <option value="llm">llm (force LLM)</option>
          </select>
        </div>

        <button
          type="submit"
          disabled={pending || !filename}
          data-testid="bucket-upload-submit"
          className="inline-flex w-full items-center justify-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3.5 py-1.5 text-xs font-bold text-ink shadow-glow transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pending ? "Uploading…" : "Upload & distill"}
        </button>
      </form>

      {result !== null && (
        <div
          data-testid="bucket-upload-result"
          data-ok={String(result.ok)}
          className={`mt-3 rounded-lg border px-3 py-2 text-xs ${
            result.ok
              ? "border-aqua/40 bg-aqua/10 text-aqua/95"
              : "border-coral/40 bg-coral/10 text-coral/95"
          }`}
        >
          {result.ok ? (
            <>
              <div className="flex items-center gap-2">
                <Badge tone={result.decision === "new" ? "ok" : "warn"}>
                  {result.decision}
                </Badge>
                <span className="font-mono text-[10px] uppercase tracking-widest text-white/60">
                  via {result.classifier}
                </span>
              </div>
              <p className="mt-1 text-white/85">{result.message}</p>
              {result.articleIds.length > 0 && (
                <p className="mt-1 font-mono text-[10px] text-white/50">
                  article {result.articleIds[0].slice(0, 8)}…
                </p>
              )}
            </>
          ) : (
            <>
              <div className="font-semibold">
                Upload failed{result.status ? ` (${result.status})` : ""}
              </div>
              <p className="mt-1 text-white/85">{result.message}</p>
            </>
          )}
        </div>
      )}
    </Card>
  );
}
