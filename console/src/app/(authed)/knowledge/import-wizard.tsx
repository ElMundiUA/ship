"use client";

import { useMemo, useState, useTransition } from "react";
import type { ReactNode } from "react";

import { Badge, Card, CardHeader } from "@/components/ui";
import type { ApiActivatedRepo } from "@/lib/api/client";
import type { ApiBucketScope, ApiIntegration } from "@/lib/api/types";

import { createImportSourceAction, type ImportSourceResult } from "./actions";
import {
  ConfluenceSectionPicker,
  type ConfluenceSectionRef,
} from "./confluence-section-picker";
import { DocsRepoTreePicker } from "./docs-repo-tree-picker";
import { NotionResourcePicker, type NotionPageRef } from "./notion-resource-picker";

type SourceKind = "website" | "notion" | "confluence" | "docs_repo" | "static_upload";

const SOURCE_OPTIONS: Array<{ kind: SourceKind; label: string; hint: string }> = [
  { kind: "website", label: "Website", hint: "Firecrawl maps and scrapes pages into Markdown." },
  { kind: "notion", label: "Notion", hint: "Use connected Notion pages/databases as a source." },
  { kind: "confluence", label: "Confluence", hint: "Use connected Confluence pages as a source." },
  { kind: "docs_repo", label: "Docs repo", hint: "Read Markdown docs from an activated repository." },
  { kind: "static_upload", label: "Uploaded files", hint: "One-shot import without periodic sync." },
];

export function KnowledgeImportWizard({
  integrations,
  repos,
  defaultScope,
  workspaceId,
}: {
  integrations: ApiIntegration[];
  repos: ApiActivatedRepo[];
  defaultScope: ApiBucketScope;
  workspaceId?: string;
}) {
  const [kind, setKind] = useState<SourceKind>("website");
  const [name, setName] = useState("Website knowledge");
  const [url, setUrl] = useState("");
  const [notionRefs, setNotionRefs] = useState<NotionPageRef[]>([]);
  const [confluenceRefs, setConfluenceRefs] = useState<ConfluenceSectionRef[]>([]);
  const [repoId, setRepoId] = useState(repos[0]?.id ?? "");
  const [repoPaths, setRepoPaths] = useState<string[]>([]);
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadBody, setUploadBody] = useState("");
  const [syncNow, setSyncNow] = useState(true);
  const [result, setResult] = useState<ImportSourceResult | null>(null);
  const [pending, startTransition] = useTransition();

  const matchingIntegrations = useMemo(
    () => integrations.filter((integration) => integration.kind === kind),
    [integrations, kind],
  );
  const [integrationId, setIntegrationId] = useState("");
  const selectedIntegrationId = integrationId || matchingIntegrations[0]?.id || "";
  const selected = SOURCE_OPTIONS.find((option) => option.kind === kind);

  function submit() {
    setResult(null);
    const parsed = buildConfig();
    if (!parsed.ok) {
      setResult({ ok: false, message: parsed.message });
      return;
    }
    startTransition(async () => {
      const response = await createImportSourceAction({
        kind,
        name,
        config: parsed.config,
        integrationId: kind === "notion" || kind === "confluence" ? selectedIntegrationId : null,
        repoId: kind === "docs_repo" ? repoId : null,
        syncNow,
      });
      setResult(response);
    });
  }

  function buildConfig():
    | { ok: true; config: Record<string, unknown> }
    | { ok: false; message: string } {
    if (kind === "website") {
      if (!url.trim()) return { ok: false, message: "Website URL is required." };
      return { ok: true, config: { url: url.trim(), limit: 25, change_tracking: true, only_main_content: true } };
    }
    if (kind === "notion") {
      if (!selectedIntegrationId) return { ok: false, message: "Connect Notion first in integrations." };
      if (notionRefs.length === 0) {
        return { ok: false, message: "Pick at least one Notion page or database from the list." };
      }
      return { ok: true, config: { resource_refs: notionRefs } };
    }
    if (kind === "confluence") {
      if (!selectedIntegrationId) return { ok: false, message: "Connect Confluence first in integrations." };
      if (confluenceRefs.length === 0) {
        return { ok: false, message: "Pick at least one section from a Confluence space." };
      }
      return { ok: true, config: { resource_refs: confluenceRefs } };
    }
    if (kind === "docs_repo") {
      if (!repoId) return { ok: false, message: "Pick a repository." };
      if (repoPaths.length === 0) {
        return { ok: false, message: "Pick at least one folder or file from the repo tree." };
      }
      return {
        ok: true,
        config: {
          paths: repoPaths,
          extensions: [".md", ".mdx", ".rst", ".txt", ".adoc"],
          limit: 50,
        },
      };
    }
    if (!uploadBody.trim()) return { ok: false, message: "Paste Markdown content for uploaded files." };
    return {
      ok: true,
      config: {
        documents: [{ title: uploadTitle.trim() || name, filename: `${slugify(uploadTitle || name)}.md`, body_md: uploadBody }],
      },
    };
  }

  return (
    <Card className="mb-6" data-testid="knowledge-import-wizard">
      <CardHeader
        title="Import source"
        subtitle="Connect a source once; Ship syncs changed items, analyzes them, and routes articles into the recommended buckets."
      />
      <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
        <div className="space-y-2">
          {SOURCE_OPTIONS.map((option) => (
            <button
              key={option.kind}
              type="button"
              onClick={() => {
                setKind(option.kind);
                setName(defaultName(option.kind));
                setResult(null);
              }}
              className={`w-full rounded-xl border px-3 py-2 text-left text-sm transition ${
                kind === option.kind
                  ? "border-aqua/50 bg-aqua/10 text-white"
                  : "border-white/10 bg-white/[0.03] text-white/65 hover:border-white/25"
              }`}
            >
              <div className="font-semibold">{option.label}</div>
              <div className="mt-1 text-xs text-white/45">{option.hint}</div>
            </button>
          ))}
        </div>
        <div className="space-y-4">
          <div className="rounded-xl border border-aqua/25 bg-aqua/[0.06] p-3 text-xs text-white/70">
            <Badge tone="neutral">workspace source</Badge>
            <span className="ml-2">{selected?.hint} Articles are auto-published into buckets with provenance and fingerprint-based skip logic.</span>
          </div>
          <Field label="Source name">
            <input value={name} onChange={(event) => setName(event.target.value)} className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 text-sm text-white/90" />
          </Field>
          {kind === "website" && (
            <Field label="Website URL" hint="Firecrawl will map URLs, scrape Markdown, and Ship will skip unchanged hashes.">
              <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://docs.example.com" className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 text-sm text-white/90" />
            </Field>
          )}
          {kind === "notion" && (
            <>
              <Field label="Notion integration">
                <select value={selectedIntegrationId} onChange={(event) => setIntegrationId(event.target.value)} className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 text-sm text-white/90">
                  {matchingIntegrations.length === 0 ? <option value="">No integration connected</option> : matchingIntegrations.map((integration) => <option key={integration.id} value={integration.id}>{integration.kind} - {integration.status}</option>)}
                </select>
              </Field>
              <Field label="Pages to import">
                {workspaceId && selectedIntegrationId ? (
                  <NotionResourcePicker
                    workspaceId={workspaceId}
                    integrationId={selectedIntegrationId}
                    value={notionRefs}
                    onChange={setNotionRefs}
                  />
                ) : (
                  <p className="text-[11px] text-white/55">
                    {workspaceId
                      ? "Connect Notion to pick pages."
                      : "Workspace not loaded yet — refresh the page."}
                  </p>
                )}
              </Field>
            </>
          )}
          {kind === "confluence" && (
            <>
              <Field label="Confluence integration">
                <select value={selectedIntegrationId} onChange={(event) => setIntegrationId(event.target.value)} className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 text-sm text-white/90">
                  {matchingIntegrations.length === 0 ? <option value="">No integration connected</option> : matchingIntegrations.map((integration) => <option key={integration.id} value={integration.id}>{integration.kind} - {integration.status}</option>)}
                </select>
              </Field>
              <Field label="Sections to import">
                {workspaceId && selectedIntegrationId ? (
                  <ConfluenceSectionPicker
                    workspaceId={workspaceId}
                    integrationId={selectedIntegrationId}
                    value={confluenceRefs}
                    onChange={setConfluenceRefs}
                  />
                ) : (
                  <p className="text-[11px] text-white/55">
                    {workspaceId
                      ? "Connect Confluence (Atlassian API token) to pick sections."
                      : "Workspace not loaded yet — refresh the page."}
                  </p>
                )}
              </Field>
            </>
          )}
          {kind === "docs_repo" && (
            <>
              <Field label="Repository">
                <select value={repoId} onChange={(event) => setRepoId(event.target.value)} className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 text-sm text-white/90">
                  {repos.length === 0 ? <option value="">No activated repos</option> : repos.map((repo) => <option key={repo.id} value={repo.id}>{repo.full_name}</option>)}
                </select>
              </Field>
              <Field label="Docs to import">
                {workspaceId && repoId ? (
                  <DocsRepoTreePicker
                    workspaceId={workspaceId}
                    repoId={repoId}
                    value={repoPaths}
                    onChange={setRepoPaths}
                  />
                ) : (
                  <p className="text-[11px] text-white/55">
                    {workspaceId ? "Pick a repository first." : "Workspace not loaded yet — refresh the page."}
                  </p>
                )}
              </Field>
            </>
          )}
          {kind === "static_upload" && (
            <>
              <Field label="Document title"><input value={uploadTitle} onChange={(event) => setUploadTitle(event.target.value)} className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 text-sm text-white/90" /></Field>
              <Field label="Markdown content" hint="This source is one-shot and has no scheduled sync."><textarea value={uploadBody} onChange={(event) => setUploadBody(event.target.value)} rows={7} className="w-full rounded border border-white/15 bg-black/30 px-3 py-2 font-mono text-[12px] text-aqua/85" /></Field>
            </>
          )}
          <label className="flex items-center gap-2 text-xs text-white/70">
            <input type="checkbox" checked={syncNow} onChange={(event) => setSyncNow(event.target.checked)} className="accent-aqua" />
            Sync immediately after creating the source
          </label>
          <div className="flex justify-end"><button type="button" disabled={pending} onClick={submit} className="rounded-full border border-aqua/50 bg-aqua/15 px-4 py-2 text-sm font-bold text-aqua transition hover:bg-aqua/25 disabled:cursor-not-allowed disabled:opacity-50">{pending ? "Creating..." : "Create source"}</button></div>
          {result && <ResultBox result={result} />}
          <div className="text-[11px] text-white/45">Default scope: {defaultScope}. Sources route into workspace buckets.</div>
        </div>
      </div>
    </Card>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="block space-y-1"><span className="text-xs font-semibold uppercase tracking-wider text-white/55">{label}</span>{children}{hint && <span className="block text-[11px] text-white/45">{hint}</span>}</label>;
}

function ResultBox({ result }: { result: ImportSourceResult }) {
  if (!result.ok) return <div className="rounded-xl border border-coral/35 bg-coral/10 p-3 text-sm text-coral">{result.message}</div>;
  // A "synced" run with all-zero stats means we accepted the source but
  // nothing got pulled — usually misconfigured selection. Don't dress it
  // up as a green success: amber + a hint so the operator sees something
  // is off instead of trusting the checkmark.
  const stats = result.stats as { discovered?: number; notes_created?: number; errors?: number } | undefined;
  const emptyHarvest = result.synced
    && stats
    && (stats.discovered ?? 0) === 0
    && (stats.notes_created ?? 0) === 0
    && (stats.errors ?? 0) === 0;
  if (emptyHarvest) {
    return (
      <div className="rounded-xl border border-amber-300/40 bg-amber-300/10 p-3 text-sm text-amber-100">
        Source created, but the sync discovered nothing. Double-check the resources you selected — the integration may not have access, or the picks were empty.
        <pre className="mt-2 overflow-x-auto text-[11px] text-amber-100/80">{JSON.stringify(result.stats, null, 2)}</pre>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-aqua/30 bg-aqua/10 p-3 text-sm text-aqua">
      Source created{result.synced ? " and synced" : ""}.
      {result.syncError && <div className="mt-1 text-xs text-coral">Sync failed: {result.syncError}</div>}
      {result.stats && <pre className="mt-2 overflow-x-auto text-[11px] text-aqua/80">{JSON.stringify(result.stats, null, 2)}</pre>}
    </div>
  );
}

function defaultName(kind: SourceKind): string {
  if (kind === "website") return "Website knowledge";
  if (kind === "docs_repo") return "Repository docs";
  if (kind === "static_upload") return "Uploaded knowledge";
  return `${kind[0]?.toUpperCase() ?? ""}${kind.slice(1)} knowledge`;
}

function slugify(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80) || "upload";
}
