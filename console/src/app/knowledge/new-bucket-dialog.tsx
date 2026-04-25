"use client";

/**
 * "+ New bucket" client dialog for `/knowledge`.
 *
 * Three modes:
 * - **Upload bucket** (`external_static`) — name + description +
 *   scope. User will upload files from the detail page afterwards.
 * - **Connector bucket** (`connector_proxy`) — same + integration
 *   picker + free-form ``resource_ref`` (Confluence space key, Notion
 *   database id, etc.). The backend validates the integration id
 *   against the current workspace before persisting.
 * - **Guided import** — a curated Notion/Confluence page list capped
 *   at a small batch size so operators split large docs into valuable
 *   buckets instead of importing whole workspaces by accident.
 *
 * The dialog is a plain inline panel rather than a portal/modal so
 * it doesn't fight keyboard focus / scroll-lock semantics that would
 * require heavier UI primitives. Submit flows through a server action
 * that redirects to the fresh bucket's detail page on success.
 */

import { useState, useTransition } from "react";

import { Badge, Card, CardHeader } from "@/components/ui";
import type { ApiIntegration, ApiBucketScope } from "@/lib/api/types";

import {
  createBucketAction,
  createGuidedImportAction,
  type CreateBucketResult,
  type GuidedImportResult,
} from "./actions";

type Kind = "external_static" | "connector_proxy" | "guided_import";

const GUIDED_IMPORT_MAX_BUCKETS = 20;
const DEFAULT_GUIDED_IMPORT = `Runbook overview | notion-or-confluence-page-id | runbook-overview
Incident response | second-page-id`;

export function NewBucketDialog({
  integrations,
  defaultScope = "workspace",
}: {
  integrations: ApiIntegration[];
  defaultScope?: ApiBucketScope;
}) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<Kind>("external_static");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [integrationId, setIntegrationId] = useState<string>(
    integrations[0]?.id ?? "",
  );
  const [resourceRefJson, setResourceRefJson] = useState<string>(
    '{\n  "database_id": ""\n}',
  );
  const [guidedIntegrationId, setGuidedIntegrationId] = useState<string>(
    integrations.find((i) => i.kind === "notion" || i.kind === "confluence")?.id ??
      "",
  );
  const [guidedInput, setGuidedInput] = useState(DEFAULT_GUIDED_IMPORT);

  const [result, setResult] = useState<CreateBucketResult | null>(null);
  const [guidedResult, setGuidedResult] = useState<GuidedImportResult | null>(
    null,
  );
  const [pending, startTransition] = useTransition();

  function reset() {
    setName("");
    setSlug("");
    setDescription("");
    setResult(null);
    setGuidedResult(null);
  }

  function submit() {
    setResult(null);
    setGuidedResult(null);
    if (kind === "guided_import") {
      const parsed = parseGuidedImport(guidedInput);
      if (parsed.error) {
        setGuidedResult({ ok: false, message: parsed.error });
        return;
      }
      if (!guidedIntegrationId) {
        setGuidedResult({
          ok: false,
          message: "Pick a Notion or Confluence integration.",
        });
        return;
      }
      if (parsed.rows.length > GUIDED_IMPORT_MAX_BUCKETS) {
        setGuidedResult({
          ok: false,
          message: `Too many roots selected (${parsed.rows.length}). Keep this import under ${GUIDED_IMPORT_MAX_BUCKETS} buckets and split the rest into a separate pass.`,
        });
        return;
      }
      startTransition(async () => {
        const res = await createGuidedImportAction({
          scope: defaultScope,
          items: parsed.rows.map((row) => ({
            title: row.title,
            slug: row.slug,
            description: `Imported from ${selectedGuidedIntegration?.kind ?? "connector"} page ${row.pageId}.`,
            integrationId: guidedIntegrationId,
            resourceRef: { page_id: row.pageId },
          })),
        });
        setGuidedResult(res);
      });
      return;
    }

    let resourceRef: Record<string, unknown> | undefined;
    if (kind === "connector_proxy") {
      try {
        const parsed = JSON.parse(resourceRefJson);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          throw new Error("resource_ref must be a JSON object");
        }
        resourceRef = parsed as Record<string, unknown>;
      } catch (err) {
        setResult({
          ok: false,
          message: `resource_ref JSON invalid: ${err instanceof Error ? err.message : String(err)}`,
        });
        return;
      }
    }

    startTransition(async () => {
      const res = await createBucketAction({
        kind,
        name,
        slug: slug || undefined,
        description: description || undefined,
        scope: defaultScope,
        integrationId: kind === "connector_proxy" ? integrationId : undefined,
        resourceRef,
      });
      // Only reached on failure — the action redirects on success.
      setResult(res);
    });
  }

  if (!open) {
    return (
      <button
        type="button"
        data-testid="new-bucket-open"
        onClick={() => {
          setOpen(true);
          reset();
        }}
        className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3.5 py-1.5 text-xs font-bold text-ink shadow-glow transition hover:brightness-110"
      >
        + New bucket
      </button>
    );
  }

  const connectorUnavailable = integrations.length === 0;
  const knowledgeIntegrations = integrations.filter(
    (i) => i.kind === "notion" || i.kind === "confluence",
  );
  const guidedUnavailable = knowledgeIntegrations.length === 0;
  const guidedPreview = parseGuidedImport(guidedInput);
  const selectedGuidedIntegration = knowledgeIntegrations.find(
    (i) => i.id === guidedIntegrationId,
  );
  const guidedOverLimit =
    !guidedPreview.error && guidedPreview.rows.length > GUIDED_IMPORT_MAX_BUCKETS;

  return (
    <Card className="mb-6" data-testid="new-bucket-dialog">
      <CardHeader
        title="Create a new bucket"
        subtitle="Upload bucket hosts files you paste in; connector bucket mirrors a third-party source."
        action={
          <button
            type="button"
            data-testid="new-bucket-close"
            onClick={() => setOpen(false)}
            className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/75 transition hover:border-white/30 hover:text-white"
          >
            Cancel
          </button>
        }
      />

      <div className="mb-4 flex gap-2">
        <KindPill
          active={kind === "external_static"}
          label="Upload bucket"
          onClick={() => setKind("external_static")}
          testId="new-bucket-kind-upload"
        />
        <KindPill
          active={kind === "connector_proxy"}
          label="Connector bucket"
          onClick={() => {
            if (!connectorUnavailable) setKind("connector_proxy");
          }}
          disabled={connectorUnavailable}
          testId="new-bucket-kind-connector"
        />
        <KindPill
          active={kind === "guided_import"}
          label="Guided import"
          onClick={() => {
            if (!guidedUnavailable) setKind("guided_import");
          }}
          disabled={guidedUnavailable}
          testId="new-bucket-kind-guided-import"
        />
      </div>

      {connectorUnavailable && kind === "external_static" && (
        <p className="mb-3 text-[11px] text-white/50">
          No integrations configured yet — connector buckets become
          available once you wire a connector under{" "}
          <code className="rounded bg-white/[0.06] px-1 font-mono text-aqua/85">
            /settings
          </code>
          .
        </p>
      )}

      {guidedUnavailable && (
        <p className="mb-3 text-[11px] text-white/50">
          Guided import needs a Notion or Confluence integration first.
        </p>
      )}

      {kind === "guided_import" ? (
        <GuidedImportPanel
          integrations={knowledgeIntegrations}
          integrationId={guidedIntegrationId}
          onIntegrationChange={setGuidedIntegrationId}
          value={guidedInput}
          onChange={setGuidedInput}
          preview={guidedPreview}
          overLimit={guidedOverLimit}
          result={guidedResult}
        />
      ) : (
        <div className="grid grid-cols-1 gap-3">
          <Field label="Name">
            <input
              data-testid="new-bucket-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="On-call runbooks"
              className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 text-sm text-white/90"
            />
          </Field>

          <Field
            label="Slug (optional)"
            hint="Lowercase letters, numbers, hyphens. Derived from name if blank."
          >
            <input
              data-testid="new-bucket-slug"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="oncall-runbooks"
              className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 font-mono text-xs text-aqua/85"
            />
          </Field>

          <Field label="Description (optional)">
            <textarea
              data-testid="new-bucket-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 text-sm text-white/90"
            />
          </Field>

          {kind === "connector_proxy" && (
            <>
              <Field
                label="Integration"
                hint="Pick which integration this bucket mirrors. Create more under /settings."
              >
                <select
                  data-testid="new-bucket-integration"
                  value={integrationId}
                  onChange={(e) => setIntegrationId(e.target.value)}
                  className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 text-sm text-white/90"
                >
                  {integrations.map((i) => (
                    <option key={i.id} value={i.id}>
                      {i.kind} — {i.status}
                      {i.has_secret ? "" : " (no secret)"}
                    </option>
                  ))}
                </select>
              </Field>

              <Field
                label="resource_ref (JSON)"
                hint="Source-specific handle. Notion: { database_id }. Confluence: { space_key }. Free-form per connector."
              >
                <textarea
                  data-testid="new-bucket-resource-ref"
                  value={resourceRefJson}
                  onChange={(e) => setResourceRefJson(e.target.value)}
                  rows={4}
                  className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 font-mono text-[12px] text-aqua/85"
                />
              </Field>
            </>
          )}
        </div>
      )}

      <div className="mt-5 flex items-center gap-3">
        <button
          type="button"
          data-testid="new-bucket-submit"
          onClick={submit}
          disabled={
            pending ||
            (kind === "guided_import"
              ? guidedUnavailable || Boolean(guidedPreview.error) || guidedOverLimit
              : name.trim().length === 0 ||
                (kind === "connector_proxy" && !integrationId))
          }
          className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3.5 py-1.5 text-xs font-bold text-ink shadow-glow transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pending
            ? "Creating…"
            : kind === "guided_import"
              ? "Create curated buckets"
              : kind === "connector_proxy"
                ? "Create connector bucket"
                : "Create upload bucket"}
        </button>

        {kind !== "guided_import" && result && !result.ok && (
          <div
            data-testid="new-bucket-error"
            className="rounded border border-coral/40 bg-coral/10 px-3 py-1.5 text-xs text-coral/95"
          >
            <Badge tone="err">error</Badge>{" "}
            {result.status ? `(${result.status}) ` : ""}
            {result.message}
          </div>
        )}
      </div>
    </Card>
  );
}

function KindPill({
  active,
  label,
  onClick,
  disabled,
  testId,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  testId?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      data-active={String(active)}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${
        active
          ? "border-aqua/60 bg-aqua/15 text-aqua"
          : "border-white/15 bg-white/[0.02] text-white/70 hover:border-white/30 hover:text-white"
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

type GuidedRow = {
  title: string;
  pageId: string;
  slug?: string;
};

type GuidedPreview =
  | { rows: GuidedRow[]; error: null }
  | { rows: GuidedRow[]; error: string };

function GuidedImportPanel({
  integrations,
  integrationId,
  onIntegrationChange,
  value,
  onChange,
  preview,
  overLimit,
  result,
}: {
  integrations: ApiIntegration[];
  integrationId: string;
  onIntegrationChange: (id: string) => void;
  value: string;
  onChange: (value: string) => void;
  preview: GuidedPreview;
  overLimit: boolean;
  result: GuidedImportResult | null;
}) {
  return (
    <div className="grid grid-cols-1 gap-4" data-testid="guided-import-panel">
      <div className="rounded-xl border border-aqua/25 bg-aqua/[0.06] p-3 text-xs text-white/70">
        <div className="mb-1 flex items-center gap-2">
          <Badge tone="neutral">guardrail</Badge>
          <span className="font-semibold text-white/85">
            Import roots, not the whole workspace.
          </span>
        </div>
        Paste only curated Notion/Confluence page roots. Ship will create
        one connector bucket per row and refuses batches above{" "}
        {GUIDED_IMPORT_MAX_BUCKETS} roots so large spaces get split into
        intentional buckets.
      </div>

      <Field label="Source integration">
        <select
          data-testid="guided-import-integration"
          value={integrationId}
          onChange={(e) => onIntegrationChange(e.target.value)}
          className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 text-sm text-white/90"
        >
          {integrations.map((i) => (
            <option key={i.id} value={i.id}>
              {i.kind} — {i.status}
              {i.has_secret ? "" : " (no secret)"}
            </option>
          ))}
        </select>
      </Field>

      <Field
        label="Curated page roots"
        hint="One per line: Title | page_id | optional-slug. JSON array with title/page_id/slug also works."
      >
        <textarea
          data-testid="guided-import-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={6}
          className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 font-mono text-[12px] text-aqua/85"
        />
      </Field>

      <div
        data-testid="guided-import-preview"
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
                Preview: {preview.rows.length} bucket
                {preview.rows.length === 1 ? "" : "s"}
              </span>
              <Badge tone={overLimit ? "err" : "ok"}>
                limit {GUIDED_IMPORT_MAX_BUCKETS}
              </Badge>
            </div>
            {overLimit && (
              <p className="mb-2">
                Too much content for one pass. Split this into smaller,
                valuable buckets before importing.
              </p>
            )}
            <ul className="space-y-1">
              {preview.rows.slice(0, 6).map((row) => (
                <li
                  key={`${row.pageId}-${row.slug ?? row.title}`}
                  className="truncate"
                >
                  <span className="text-white/90">{row.title}</span>{" "}
                  <span className="font-mono text-aqua/75">{row.pageId}</span>
                </li>
              ))}
              {preview.rows.length > 6 && (
                <li className="text-white/45">
                  + {preview.rows.length - 6} more in this batch
                </li>
              )}
            </ul>
          </>
        )}
      </div>

      {result && (
        <div
          data-testid="guided-import-result"
          data-ok={String(result.ok)}
          className={`rounded border px-3 py-2 text-xs ${
            result.ok
              ? "border-aqua/40 bg-aqua/10 text-aqua/95"
              : "border-coral/40 bg-coral/10 text-coral/95"
          }`}
        >
          {result.ok ? (
            <>
              <Badge tone="ok">created</Badge>{" "}
              {result.created.length} curated bucket
              {result.created.length === 1 ? "" : "s"}.
            </>
          ) : (
            <>
              <Badge tone="err">error</Badge>{" "}
              {result.status ? `(${result.status}) ` : ""}
              {result.message}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function parseGuidedImport(raw: string): GuidedPreview {
  const trimmed = raw.trim();
  if (!trimmed) return { rows: [], error: "Paste at least one page root." };
  if (trimmed.startsWith("[")) return parseGuidedImportJson(trimmed);

  const rows: GuidedRow[] = [];
  for (const [index, line] of trimmed.split(/\r?\n/).entries()) {
    const clean = line.trim();
    if (!clean || clean.startsWith("#")) continue;
    const [title, pageId, slug] = clean.split("|").map((part) => part.trim());
    if (!title || !pageId) {
      return {
        rows,
        error: `Line ${index + 1} must be: Title | page_id | optional-slug.`,
      };
    }
    rows.push({ title, pageId, slug: slug || undefined });
  }
  if (rows.length === 0) {
    return { rows, error: "Paste at least one page root." };
  }
  return { rows, error: null };
}

function parseGuidedImportJson(raw: string): GuidedPreview {
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return { rows: [], error: "JSON import must be an array." };
    }
    const rows: GuidedRow[] = [];
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
