"use client";

import { useMemo, useState, useTransition } from "react";

import { Badge, Card, CardHeader } from "@/components/ui";
import type { ApiBucketScope, ApiIntegration } from "@/lib/api/types";

import {
  createGuidedImportAction,
  type GuidedImportResult,
} from "./actions";

const MAX_IMPORT_ROOTS = 20;
const EXAMPLE_ROOTS = `Runbook overview | notion-or-confluence-page-id | runbook-overview
Incident response | second-page-id`;

type WizardStep = "source" | "curate" | "review";

type RootRow = {
  title: string;
  pageId: string;
  slug?: string;
};

type RootPreview =
  | { rows: RootRow[]; error: null }
  | { rows: RootRow[]; error: string };

export function KnowledgeImportWizard({
  integrations,
  defaultScope,
}: {
  integrations: ApiIntegration[];
  defaultScope: ApiBucketScope;
}) {
  const knowledgeIntegrations = useMemo(
    () =>
      integrations.filter(
        (integration) =>
          integration.kind === "notion" || integration.kind === "confluence",
      ),
    [integrations],
  );
  const [step, setStep] = useState<WizardStep>("source");
  const [integrationId, setIntegrationId] = useState(
    knowledgeIntegrations[0]?.id ?? "",
  );
  const [roots, setRoots] = useState(EXAMPLE_ROOTS);
  const [result, setResult] = useState<GuidedImportResult | null>(null);
  const [pending, startTransition] = useTransition();

  const selectedIntegration = knowledgeIntegrations.find(
    (integration) => integration.id === integrationId,
  );
  const preview = parseRoots(roots);
  const overLimit = !preview.error && preview.rows.length > MAX_IMPORT_ROOTS;
  const canContinueFromSource = Boolean(integrationId);
  const canReview =
    canContinueFromSource &&
    !preview.error &&
    preview.rows.length > 0 &&
    !overLimit;

  function createBuckets() {
    setResult(null);
    if (!canReview || preview.error) return;
    startTransition(async () => {
      const response = await createGuidedImportAction({
        scope: defaultScope,
        items: preview.rows.map((row) => ({
          title: row.title,
          slug: row.slug,
          description: `Imported from ${selectedIntegration?.kind ?? "knowledge"} page ${row.pageId}.`,
          integrationId,
          resourceRef: { page_id: row.pageId },
        })),
      });
      setResult(response);
    });
  }

  if (knowledgeIntegrations.length === 0) {
    return (
      <Card className="mb-6" data-testid="knowledge-import-wizard-empty">
        <CardHeader
          title="Guided knowledge import"
          subtitle="Connect Notion or Confluence first, then split docs into curated buckets."
        />
        <p className="text-sm text-white/60">
          No Notion or Confluence integration is available for this workspace yet.
        </p>
      </Card>
    );
  }

  return (
    <Card className="mb-6" data-testid="knowledge-import-wizard">
      <CardHeader
        title="Guided knowledge import"
        subtitle="Curate Notion/Confluence page roots into intentional buckets before syncing."
      />

      <div className="mb-4 grid grid-cols-3 gap-2">
        <StepButton
          active={step === "source"}
          done={canContinueFromSource}
          label="1. Source"
          onClick={() => setStep("source")}
        />
        <StepButton
          active={step === "curate"}
          done={canReview}
          label="2. Curate"
          onClick={() => setStep("curate")}
        />
        <StepButton
          active={step === "review"}
          done={result?.ok === true}
          label="3. Review"
          onClick={() => setStep("review")}
        />
      </div>

      {step === "source" && (
        <section className="space-y-4">
          <GuardrailCopy />
          <Field label="Knowledge source">
            <select
              data-testid="knowledge-import-source"
              value={integrationId}
              onChange={(event) => setIntegrationId(event.target.value)}
              className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 text-sm text-white/90"
            >
              {knowledgeIntegrations.map((integration) => (
                <option key={integration.id} value={integration.id}>
                  {integration.kind} — {integration.status}
                  {integration.has_secret ? "" : " (no secret)"}
                </option>
              ))}
            </select>
          </Field>
          <div className="flex justify-end">
            <PrimaryButton
              disabled={!canContinueFromSource}
              onClick={() => setStep("curate")}
            >
              Continue to curated roots
            </PrimaryButton>
          </div>
        </section>
      )}

      {step === "curate" && (
        <section className="space-y-4">
          <Field
            label="Curated roots"
            hint="One per line: Bucket title | page_id | optional-slug. JSON array with title/page_id/slug also works."
          >
            <textarea
              data-testid="knowledge-import-roots"
              value={roots}
              onChange={(event) => {
                setRoots(event.target.value);
                setResult(null);
              }}
              rows={7}
              className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 font-mono text-[12px] text-aqua/85"
            />
          </Field>
          <PreviewBox preview={preview} overLimit={overLimit} />
          <div className="flex items-center justify-between gap-3">
            <SecondaryButton onClick={() => setStep("source")}>
              Back
            </SecondaryButton>
            <PrimaryButton disabled={!canReview} onClick={() => setStep("review")}>
              Review buckets
            </PrimaryButton>
          </div>
        </section>
      )}

      {step === "review" && (
        <section className="space-y-4">
          <ReviewBox
            provider={selectedIntegration?.kind ?? "knowledge"}
            preview={preview}
            overLimit={overLimit}
          />
          <div className="flex items-center justify-between gap-3">
            <SecondaryButton onClick={() => setStep("curate")}>
              Back
            </SecondaryButton>
            <PrimaryButton disabled={!canReview || pending} onClick={createBuckets}>
              {pending ? "Creating and syncing..." : "Create and sync buckets"}
            </PrimaryButton>
          </div>
          {result && <ResultBox result={result} />}
        </section>
      )}
    </Card>
  );
}

function GuardrailCopy() {
  return (
    <div className="rounded-xl border border-aqua/25 bg-aqua/[0.06] p-3 text-xs text-white/70">
      <div className="mb-1 flex items-center gap-2">
        <Badge tone="neutral">guardrail</Badge>
        <span className="font-semibold text-white/85">
          No bulk workspace imports.
        </span>
      </div>
      Pick valuable Notion/Confluence page roots and split large spaces into
      separate buckets. This wizard blocks batches above {MAX_IMPORT_ROOTS} roots
      before anything is created.
    </div>
  );
}

function PreviewBox({
  preview,
  overLimit,
}: {
  preview: RootPreview;
  overLimit: boolean;
}) {
  return (
    <div
      data-testid="knowledge-import-preview"
      data-over-limit={String(overLimit)}
      className={`rounded-xl border p-3 text-xs ${
        preview.error || overLimit
          ? "border-coral/40 bg-coral/10 text-coral/95"
          : "border-white/12 bg-white/[0.03] text-white/70"
      }`}
    >
      {preview.error ? (
        preview.error
      ) : (
        <>
          <div className="mb-2 flex items-center justify-between gap-3">
            <span>
              Preview: {preview.rows.length} root
              {preview.rows.length === 1 ? "" : "s"}
            </span>
            <Badge tone={overLimit ? "err" : "ok"}>limit {MAX_IMPORT_ROOTS}</Badge>
          </div>
          {overLimit && (
            <p>
              Too much content for one pass. Split this source into smaller,
              valuable buckets first.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function ReviewBox({
  provider,
  preview,
  overLimit,
}: {
  provider: string;
  preview: RootPreview;
  overLimit: boolean;
}) {
  if (preview.error) {
    return <PreviewBox preview={preview} overLimit={overLimit} />;
  }
  return (
    <div className="rounded-xl border border-white/12 bg-white/[0.03] p-3">
      <div className="mb-3 flex items-center justify-between gap-3 text-xs text-white/65">
        <span>
          {preview.rows.length} {provider} connector bucket
          {preview.rows.length === 1 ? "" : "s"} will be created.
        </span>
        <Badge tone={overLimit ? "err" : "ok"}>curated</Badge>
      </div>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
        {preview.rows.map((row) => (
          <div
            key={`${row.pageId}-${row.slug ?? row.title}`}
            className="rounded-lg border border-white/10 bg-black/20 p-3 text-xs"
          >
            <div className="font-semibold text-white/90">{row.title}</div>
            <div className="mt-1 font-mono text-aqua/80">
              slug: {row.slug || slugify(row.title)}
            </div>
            <div className="mt-1 truncate font-mono text-white/45">
              page_id: {row.pageId}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ResultBox({ result }: { result: GuidedImportResult }) {
  return (
    <div
      data-testid="knowledge-import-result"
      data-ok={String(result.ok)}
      className={`rounded border px-3 py-2 text-xs ${
        result.ok
          ? "border-aqua/40 bg-aqua/10 text-aqua/95"
          : "border-coral/40 bg-coral/10 text-coral/95"
      }`}
    >
      {result.ok ? (
        <>
          <Badge tone="ok">created</Badge> {result.created.length} bucket
          {result.created.length === 1 ? "" : "s"} created;{" "}
          {result.created.filter((item) => item.synced).length} synced into the
          index.
          {result.created.some((item) => !item.synced) && (
            <span className="mt-1 block text-white/65">
              Some sources need attention:{" "}
              {result.created
                .filter((item) => !item.synced)
                .map((item) => `${item.slug}: ${item.syncError ?? "sync failed"}`)
                .join("; ")}
            </span>
          )}
        </>
      ) : (
        <>
          <Badge tone="err">error</Badge>{" "}
          {result.status ? `(${result.status}) ` : ""}
          {result.message}
        </>
      )}
    </div>
  );
}

function StepButton({
  active,
  done,
  label,
  onClick,
}: {
  active: boolean;
  done: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      data-active={String(active)}
      onClick={onClick}
      className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
        active
          ? "border-aqua/60 bg-aqua/15 text-aqua"
          : done
            ? "border-lilac/35 bg-lilac/10 text-lilac"
            : "border-white/12 bg-white/[0.02] text-white/55 hover:border-white/25"
      }`}
    >
      {label}
    </button>
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
    <label className="block text-xs text-white/70">
      <span className="block font-semibold uppercase tracking-widest text-white/55">
        {label}
      </span>
      {hint && (
        <span className="mb-1 block text-[10px] text-white/45">{hint}</span>
      )}
      <span className="mt-1 block">{children}</span>
    </label>
  );
}

function PrimaryButton({
  disabled,
  onClick,
  children,
}: {
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3.5 py-1.5 text-xs font-bold text-ink shadow-glow transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {children}
    </button>
  );
}

function SecondaryButton({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/75 transition hover:border-white/30 hover:text-white"
    >
      {children}
    </button>
  );
}

function parseRoots(raw: string): RootPreview {
  const trimmed = raw.trim();
  if (!trimmed) return { rows: [], error: "Paste at least one page root." };
  if (trimmed.startsWith("[")) return parseRootJson(trimmed);

  const rows: RootRow[] = [];
  for (const [index, line] of trimmed.split(/\r?\n/).entries()) {
    const clean = line.trim();
    if (!clean || clean.startsWith("#")) continue;
    const [title, pageId, slug] = clean.split("|").map((part) => part.trim());
    if (!title || !pageId) {
      return {
        rows,
        error: `Line ${index + 1} must be: Bucket title | page_id | optional-slug.`,
      };
    }
    rows.push({ title, pageId, slug: slug || undefined });
  }
  if (rows.length === 0) {
    return { rows, error: "Paste at least one page root." };
  }
  return { rows, error: null };
}

function parseRootJson(raw: string): RootPreview {
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return { rows: [], error: "JSON import must be an array." };
    }
    const rows: RootRow[] = [];
    for (const [index, item] of parsed.entries()) {
      if (typeof item !== "object" || item === null || Array.isArray(item)) {
        return { rows, error: `JSON row ${index + 1} must be an object.` };
      }
      const record = item as Record<string, unknown>;
      const title = asCleanString(record.title);
      const pageId = asCleanString(record.page_id ?? record.pageId);
      const slug = asCleanString(record.slug);
      if (!title || !pageId) {
        return {
          rows,
          error: `JSON row ${index + 1} needs title and page_id.`,
        };
      }
      rows.push({ title, pageId, slug });
    }
    if (rows.length === 0) {
      return { rows, error: "Paste at least one page root." };
    }
    return { rows, error: null };
  } catch (err) {
    return {
      rows: [],
      error: `JSON import invalid: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

function asCleanString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
