"use client";

/**
 * "+ New bucket" client dialog for `/knowledge`.
 *
 * Two modes:
 * - **Upload bucket** (`external_static`) — name + description +
 *   scope. User will upload files from the detail page afterwards.
 * - **Connector bucket** (`connector_proxy`) — same + integration
 *   picker + free-form ``resource_ref`` (Confluence space key, Notion
 *   database id, etc.). The backend validates the integration id
 *   against the current workspace before persisting.
 *
 * The dialog is a plain inline panel rather than a portal/modal so
 * it doesn't fight keyboard focus / scroll-lock semantics that would
 * require heavier UI primitives. Submit flows through a server action
 * that redirects to the fresh bucket's detail page on success.
 */

import { useState, useTransition } from "react";

import { Badge, Card, CardHeader } from "@/components/ui";
import type { ApiIntegration, ApiBucketScope } from "@/lib/api/types";

import { createBucketAction, type CreateBucketResult } from "./actions";

type Kind = "external_static" | "connector_proxy";

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

  const [result, setResult] = useState<CreateBucketResult | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setName("");
    setSlug("");
    setDescription("");
    setResult(null);
  }

  function submit() {
    setResult(null);
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

        <Field label="Slug (optional)" hint="Lowercase letters, numbers, hyphens. Derived from name if blank.">
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

      <div className="mt-5 flex items-center gap-3">
        <button
          type="button"
          data-testid="new-bucket-submit"
          onClick={submit}
          disabled={
            pending ||
            name.trim().length === 0 ||
            (kind === "connector_proxy" && !integrationId)
          }
          className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3.5 py-1.5 text-xs font-bold text-ink shadow-glow transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pending
            ? "Creating…"
            : kind === "connector_proxy"
              ? "Create connector bucket"
              : "Create upload bucket"}
        </button>

        {result && !result.ok && (
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
