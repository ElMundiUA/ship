"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import {
  PlayCard,
  resolvePlayMode,
  type CardState,
  type PlayCardLastRun,
} from "@/components/play-card";
import { buildHref } from "@/components/plays/category-sidebar";
import { Card, CardHeader } from "@/components/ui";
import type {
  ApiActivatedRepo,
  ApiCatalogPattern,
  ApiLaneCatalogEntry,
  LatestRunForPlay,
} from "@/lib/api/client";

/**
 * Client-side grid for the unified ``/plays`` catalog.
 *
 * The page (a server component) is responsible for parsing the URL
 * filter state, fetching the catalog + the latest-run-per-play map,
 * and computing the visible play list. The grid is purely
 * presentational — it renders the cards, owns the inline-dispatch
 * state machine, and forwards card-body clicks to the URL via
 * ``router.push`` so the Play detail drawer (P4-02) opens
 * deep-linkably.
 *
 * Two upstream shapes converge into one card list:
 *
 * - ``ApiCatalogPattern`` (request-mode) — full dispatch capability;
 *   the card shows both **Run now** + **Automate** CTAs.
 * - ``ApiLaneCatalogEntry`` (lane recipes) — one-shot dispatch
 *   doesn't apply, so we show only the **Automate** CTA.
 *
 * Dispatch reuses ``POST /api/requests`` (same proxy
 * ``RequestsCatalog`` already calls) so the backend contract
 * doesn't move.
 */

export type UnifiedPlay =
  | {
      kind: "request";
      id: string;
      title: string;
      description: string;
      tags: string[];
      pattern: ApiCatalogPattern;
    }
  | {
      kind: "lane";
      id: string;
      title: string;
      description: string;
      tags: string[];
      entry: ApiLaneCatalogEntry;
    };

export function PlaysGrid({
  workspaceId,
  repos,
  visiblePlays,
  lastRunByPlay,
  selectedCategory,
  selectedSubcategory,
  criticalOnly,
}: {
  workspaceId: string;
  repos: ApiActivatedRepo[];
  visiblePlays: UnifiedPlay[];
  lastRunByPlay: Map<string, LatestRunForPlay>;
  selectedCategory: string;
  selectedSubcategory: string | null;
  criticalOnly: boolean;
}) {
  const router = useRouter();
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [cardState, setCardState] = useState<CardState>({ mode: "idle" });
  const [repoId, setRepoId] = useState<string>(repos[0]?.id ?? "");

  const criticalToggleHref = useMemo(
    () =>
      buildHref({
        category: selectedCategory,
        subcategory: selectedSubcategory ?? undefined,
        criticalOnly: !criticalOnly,
      }),
    [selectedCategory, selectedSubcategory, criticalOnly],
  );

  if (repos.length === 0) {
    return (
      <Card>
        <CardHeader
          title="Activate a repo first"
          subtitle="Plays dispatch / install against a specific repo. Finish onboarding to enable the grid."
        />
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="Active repo"
          subtitle="Run now dispatches against this repo's default branch; Automate opens a PR there."
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <select
            value={repoId}
            onChange={(e) => setRepoId(e.target.value)}
            className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 text-sm text-white focus:border-aqua focus:outline-none sm:w-auto"
          >
            {repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.full_name}
              </option>
            ))}
          </select>
          <a
            href={criticalToggleHref}
            aria-pressed={criticalOnly}
            className={
              "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[11px] font-semibold transition " +
              (criticalOnly
                ? "border-coral/60 bg-coral/[0.10] text-coral hover:bg-coral/[0.15]"
                : "border-white/15 bg-white/[0.04] text-white/70 hover:border-white/30 hover:text-white")
            }
            title={
              criticalOnly
                ? "Showing critical plays only — click to clear."
                : "Filter to plays marked `critical: true` in their frontmatter."
            }
          >
            <span
              aria-hidden
              className={
                "inline-block h-1.5 w-1.5 rounded-full " +
                (criticalOnly ? "bg-coral" : "bg-white/30")
              }
            />
            Critical only
          </a>
        </div>
      </Card>

      {visiblePlays.length === 0 ? (
        <EmptyCategoryState
          selectedCategory={selectedCategory}
          criticalOnly={criticalOnly}
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {visiblePlays.map((play) => {
            const expanded = openKey === play.id;
            const state = expanded ? cardState : { mode: "idle" as const };
            const onToggle = () => {
              setOpenKey(expanded ? null : play.id);
              setCardState({ mode: "idle" });
            };
            const hit = lastRunByPlay.get(play.id);
            const lastRun: PlayCardLastRun | null = hit
              ? { run: hit.run, pipelineId: hit.pipelineId }
              : null;
            const onCardClick = () => {
              router.push(
                buildHref({
                  category: selectedCategory,
                  subcategory: selectedSubcategory ?? undefined,
                  criticalOnly,
                  play: play.id,
                }),
                { scroll: false },
              );
            };

            if (play.kind === "request") {
              return (
                <PlayCard
                  key={play.id}
                  id={play.id}
                  title={play.title}
                  description={play.description}
                  tags={play.tags}
                  mode={resolvePlayMode(play.pattern.modes)}
                  pattern={play.pattern}
                  ctaLayout={{ showRunNow: true, showAutomate: true }}
                  expanded={expanded}
                  state={state}
                  onToggle={onToggle}
                  onCardClick={onCardClick}
                  lastRun={lastRun}
                  onSubmit={async (inputs) => {
                    setCardState({ mode: "saving" });
                    try {
                      const res = await fetch("/api/requests", {
                        method: "POST",
                        headers: { "content-type": "application/json" },
                        body: JSON.stringify({
                          workspaceId,
                          repoId,
                          pattern_id: play.pattern.id,
                          inputs,
                        }),
                      });
                      const data = (await res.json()) as {
                        id?: string;
                        error?: string;
                        code?: string;
                      };
                      if (!res.ok || !data.id) {
                        setCardState({
                          mode: "error",
                          message: data.error || `HTTP ${res.status}`,
                          code: data.code,
                        });
                        return;
                      }
                      window.location.reload();
                    } catch (err) {
                      setCardState({
                        mode: "error",
                        message:
                          err instanceof Error ? err.message : "Unknown error",
                      });
                    }
                  }}
                />
              );
            }

            return (
              <PlayCard
                key={play.id}
                id={play.id}
                title={play.title}
                description={play.description}
                tags={play.tags}
                mode={resolvePlayMode(undefined, {
                  event: play.entry.event,
                  schedule: play.entry.schedule,
                })}
                ctaLayout={{ showRunNow: false, showAutomate: true }}
                expanded={false}
                state={{ mode: "idle" }}
                onToggle={() => undefined}
                onCardClick={onCardClick}
                lastRun={lastRun}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

function EmptyCategoryState({
  selectedCategory,
  criticalOnly,
}: {
  selectedCategory: string;
  criticalOnly: boolean;
}) {
  if (selectedCategory === "all" && !criticalOnly) {
    return (
      <Card>
        <CardHeader
          title="Catalog is empty"
          subtitle="No plays are exposed by the backend right now. Run `shipctl sync` or check your catalog."
        />
      </Card>
    );
  }
  const allHref = buildHref({ category: "all" });
  const codeReviewHref = buildHref({ category: "code_review" });
  const criticalHref = buildHref({
    category: selectedCategory,
    criticalOnly: false,
  });
  return (
    <Card>
      <CardHeader
        title="No plays in this category"
        subtitle="Try widening the filter — the catalog likely has plays under a sibling category."
      />
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <a
          href={allHref}
          className="rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1 font-semibold text-aqua hover:bg-aqua/20"
        >
          All plays
        </a>
        <a
          href={codeReviewHref}
          className="rounded-full border border-white/15 bg-white/[0.04] px-3 py-1 font-semibold text-white/80 hover:border-white/30 hover:text-white"
        >
          Code review
        </a>
        {criticalOnly && (
          <a
            href={criticalHref}
            className="rounded-full border border-coral/40 bg-coral/[0.08] px-3 py-1 font-semibold text-coral hover:bg-coral/[0.15]"
          >
            Clear &ldquo;Critical only&rdquo;
          </a>
        )}
      </div>
    </Card>
  );
}
