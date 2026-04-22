"use client";

/**
 * Client island for the workspace Knowledge page (PR-7A).
 *
 * Owns two tabs (``Search`` + ``Canonical``) plus the POST to
 * ``/api/knowledge/search``. Canonical data is hydrated from the
 * server component; search results are fetched on demand and kept
 * local — no router churn per keystroke.
 */

import Link from "next/link";
import { useMemo, useState, useTransition } from "react";

import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  EmptyState,
} from "@/components/ui";
import type {
  ApiActivatedRepo,
  ApiKnowledgeCanonicalResponse,
  ApiKnowledgeSearchHit,
  ApiKnowledgeSearchResponse,
} from "@/lib/api/client";

import { CandidatesPanel } from "./candidates-panel";

type Props = {
  workspaceId: string;
  canonical: ApiKnowledgeCanonicalResponse;
  repos: ApiActivatedRepo[];
};

type Tab = "search" | "canonical" | "promote";

const GROUP_LABEL: Record<ApiKnowledgeSearchHit["rank_bucket"], string> = {
  repo_match: "In this repo",
  workspace: "Workspace canonical",
  other_repo: "From other repos",
};

const GROUP_ORDER: ApiKnowledgeSearchHit["rank_bucket"][] = [
  "repo_match",
  "workspace",
  "other_repo",
];

export function KnowledgeTabs({ workspaceId, canonical, repos }: Props) {
  const [tab, setTab] = useState<Tab>("search");

  return (
    <div className="flex flex-col gap-4">
      <div
        role="tablist"
        aria-label="Knowledge tabs"
        className="flex items-center gap-1 border-b border-white/10"
      >
        <TabButton active={tab === "search"} onClick={() => setTab("search")}>
          Search
        </TabButton>
        <TabButton
          active={tab === "canonical"}
          onClick={() => setTab("canonical")}
        >
          Canonical
        </TabButton>
        <TabButton
          active={tab === "promote"}
          onClick={() => setTab("promote")}
        >
          Promote candidates
        </TabButton>
      </div>

      {tab === "search" && (
        <SearchTab workspaceId={workspaceId} repos={repos} />
      )}
      {tab === "canonical" && <CanonicalTab canonical={canonical} />}
      {tab === "promote" && <CandidatesPanel workspaceId={workspaceId} />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={
        "relative px-3 py-2 text-sm font-semibold transition " +
        (active
          ? "text-white after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:rounded-full after:bg-aqua"
          : "text-white/55 hover:text-white")
      }
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Search tab
// ---------------------------------------------------------------------------

function SearchTab({
  workspaceId,
  repos,
}: {
  workspaceId: string;
  repos: ApiActivatedRepo[];
}) {
  const [query, setQuery] = useState("");
  const [repoId, setRepoId] = useState<string>("");
  const [hits, setHits] = useState<ApiKnowledgeSearchHit[] | null>(null);
  const [queriedFor, setQueriedFor] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const grouped = useMemo(() => {
    if (!hits) return null;
    const groups: Record<
      ApiKnowledgeSearchHit["rank_bucket"],
      ApiKnowledgeSearchHit[]
    > = { repo_match: [], workspace: [], other_repo: [] };
    for (const h of hits) groups[h.rank_bucket].push(h);
    return groups;
  }, [hits]);

  function runSearch() {
    const q = query.trim();
    if (q.length === 0) return;
    setError(null);
    startTransition(async () => {
      try {
        const res = await fetch("/api/knowledge/search", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            workspaceId,
            query: q,
            repoId: repoId || null,
            limit: 20,
          }),
        });
        const body = (await res.json()) as
          | ApiKnowledgeSearchResponse
          | { error?: string; code?: string };
        if (!res.ok) {
          const msg =
            ("error" in body && body.error) ||
            (res.status === 412
              ? "Workspace embeddings aren't configured yet."
              : `Search failed (HTTP ${res.status}).`);
          setHits([]);
          setQueriedFor(q);
          setError(msg);
          return;
        }
        const payload = body as ApiKnowledgeSearchResponse;
        setHits(payload.hits);
        setQueriedFor(payload.query);
      } catch (err) {
        setHits([]);
        setQueriedFor(q);
        setError(err instanceof Error ? err.message : "Search failed.");
      }
    });
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    runSearch();
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <form onSubmit={onSubmit} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-[10px] font-bold uppercase tracking-widest text-white/50">
              Search workspace knowledge
            </span>
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. how do we rotate on-call?"
              className="rounded-lg border border-white/15 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none transition focus:border-aqua/60"
            />
          </label>

          <div className="flex flex-wrap items-end gap-3">
            <label className="flex min-w-[18rem] flex-1 flex-col gap-1.5">
              <span className="text-[10px] font-bold uppercase tracking-widest text-white/50">
                Boost results from
              </span>
              <select
                value={repoId}
                onChange={(e) => setRepoId(e.target.value)}
                className="rounded-lg border border-white/15 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none transition focus:border-aqua/60"
              >
                <option value="">(no repo boost)</option>
                {repos.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.full_name}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex items-center gap-2">
              <ButtonPrimary type="submit">
                {pending ? "Searching…" : "Search"}
              </ButtonPrimary>
              {hits !== null && (
                <ButtonGhost
                  onClick={() => {
                    setHits(null);
                    setQueriedFor("");
                    setError(null);
                  }}
                >
                  Clear
                </ButtonGhost>
              )}
            </div>
          </div>
        </form>
      </Card>

      {error && (
        <Card className="border-coral/40 bg-coral/5">
          <p className="text-sm text-coral">{error}</p>
        </Card>
      )}

      {hits !== null && grouped && !error && (
        <>
          {hits.length === 0 ? (
            <EmptyState
              title="No matches"
              body={
                queriedFor
                  ? `Nothing matched "${queriedFor}" in this workspace's articles or repo chunks.`
                  : "No matches."
              }
            />
          ) : (
            <div className="flex flex-col gap-4">
              {GROUP_ORDER.map((bucket) =>
                grouped[bucket].length > 0 ? (
                  <HitGroup
                    key={bucket}
                    bucket={bucket}
                    hits={grouped[bucket]}
                  />
                ) : null,
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function HitGroup({
  bucket,
  hits,
}: {
  bucket: ApiKnowledgeSearchHit["rank_bucket"];
  hits: ApiKnowledgeSearchHit[];
}) {
  return (
    <Card padded={false}>
      <CardHeader
        className="px-5 pt-5"
        title={`${GROUP_LABEL[bucket]} · ${hits.length}`}
      />
      <ul className="divide-y divide-white/5">
        {hits.map((h) => (
          <HitRow key={`${h.source}:${h.id}`} hit={h} />
        ))}
      </ul>
    </Card>
  );
}

function HitRow({ hit }: { hit: ApiKnowledgeSearchHit }) {
  const title =
    hit.title?.trim() ||
    hit.bucket_slug ||
    (hit.source === "kb_chunk" ? "Repo chunk" : "Article");
  const href =
    hit.source === "bucket_article" && hit.bucket_slug
      ? `/knowledge/${encodeURIComponent(hit.bucket_slug)}`
      : null;

  return (
    <li className="px-5 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {href ? (
              <Link
                href={href}
                className="truncate text-sm font-semibold text-white hover:text-aqua"
              >
                {title}
              </Link>
            ) : (
              <span className="truncate text-sm font-semibold text-white">
                {title}
              </span>
            )}
            <Badge tone={scopeTone(hit.scope_kind)}>{hit.scope_kind}</Badge>
            <Badge tone="neutral">
              {hit.source === "bucket_article" ? "article" : "chunk"}
            </Badge>
            {hit.repo_full_name && (
              <span className="truncate font-mono text-[11px] text-white/55">
                {hit.repo_full_name}
              </span>
            )}
          </div>
          {hit.snippet && (
            <p className="mt-1 line-clamp-2 text-xs leading-snug text-white/65">
              {hit.snippet}
            </p>
          )}
        </div>
        <span
          className="shrink-0 font-mono text-[11px] text-aqua/80"
          title="cosine similarity"
        >
          {hit.score.toFixed(3)}
        </span>
      </div>
    </li>
  );
}

function scopeTone(
  scope: ApiKnowledgeSearchHit["scope_kind"],
): "workspace" | "project" | "neutral" {
  if (scope === "workspace") return "workspace";
  if (scope === "project") return "project";
  return "neutral";
}

// ---------------------------------------------------------------------------
// Canonical tab
// ---------------------------------------------------------------------------

function CanonicalTab({
  canonical,
}: {
  canonical: ApiKnowledgeCanonicalResponse;
}) {
  return (
    <div className="flex flex-col gap-4">
      <Card padded={false}>
        <CardHeader
          className="px-5 pt-5"
          title="Workspace canonical knowledge"
          subtitle="Buckets at workspace scope — the single source of truth per slug."
        />
        {canonical.canonical.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-white/60">
            No workspace-scope buckets yet. Promote a repo-scope bucket
            or create one via the CLI to get started.
          </p>
        ) : (
          <ul className="divide-y divide-white/5">
            {canonical.canonical.map((b) => (
              <li key={b.id} className="px-5 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Link
                        href={`/knowledge/${encodeURIComponent(b.slug)}`}
                        className="truncate text-sm font-semibold text-white hover:text-aqua"
                      >
                        {b.name}
                      </Link>
                      <span className="font-mono text-[11px] text-aqua/80">
                        {b.slug}
                      </span>
                    </div>
                    {b.description && (
                      <p className="mt-1 line-clamp-2 text-xs text-white/60">
                        {b.description}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge tone="neutral">
                      {b.article_count} article
                      {b.article_count === 1 ? "" : "s"}
                    </Badge>
                    <Badge tone={b.override_count > 0 ? "warn" : "neutral"}>
                      {b.override_count} repo override
                      {b.override_count === 1 ? "" : "s"}
                    </Badge>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card padded={false}>
        <CardHeader
          className="px-5 pt-5"
          title="Orphan slugs — candidates for promotion"
          subtitle="Slugs that appear at repo scope in ≥2 repos without a workspace-scope copy."
        />
        <p className="px-5 pt-1 text-[11px] text-white/45">
          Promote candidates analysis ships in PR-7B.
        </p>
        {canonical.orphan_slugs.length === 0 ? (
          <p className="px-5 pb-5 pt-3 text-sm text-white/60">
            No orphan slugs right now — every repeated slug already has
            a workspace-scope home.
          </p>
        ) : (
          <ul className="divide-y divide-white/5">
            {canonical.orphan_slugs.map((o) => (
              <li
                key={o.slug}
                className="flex items-center justify-between gap-3 px-5 py-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate font-mono text-sm font-semibold text-white">
                      {o.slug}
                    </span>
                    <Badge tone="info">{o.repo_count} repos</Badge>
                  </div>
                  {o.sample_repo_full_name && (
                    <p className="mt-1 text-[11px] text-white/55">
                      e.g.{" "}
                      <span className="font-mono text-white/70">
                        {o.sample_repo_full_name}
                      </span>
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
