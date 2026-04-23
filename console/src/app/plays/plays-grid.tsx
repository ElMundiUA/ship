"use client";

import { useMemo, useState } from "react";

import {
  PlayCard,
  resolvePlayMode,
  type CardState,
} from "@/components/play-card";
import { Card, CardHeader } from "@/components/ui";
import type {
  ApiActivatedRepo,
  ApiCatalogPattern,
  ApiLaneCatalogEntry,
} from "@/lib/api/client";

/**
 * Client-side grid for the unified ``/plays`` catalog.
 *
 * Merges two upstream shapes into one ``PlayCard`` list:
 *
 * - ``ApiCatalogPattern`` (request-mode) — fully dispatch-capable;
 *   the card shows both **Run now** + **Automate** CTAs.
 * - ``ApiLaneCatalogEntry`` (lane recipes) — one-shot dispatch
 *   doesn't apply, so we show only the **Automate** CTA per the
 *   ticket spec.
 *
 * Dispatch reuses ``POST /api/requests`` (same proxy
 * ``RequestsCatalog`` already calls) so the backend contract
 * doesn't move.
 */

type UnifiedPlay =
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
  lanes,
  requestPatterns,
  selectedCategory,
}: {
  workspaceId: string;
  repos: ApiActivatedRepo[];
  lanes: ApiLaneCatalogEntry[];
  requestPatterns: ApiCatalogPattern[];
  selectedCategory: string;
}) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [cardState, setCardState] = useState<CardState>({ mode: "idle" });
  const [repoId, setRepoId] = useState<string>(repos[0]?.id ?? "");

  const plays = useMemo(() => mergePlays(lanes, requestPatterns), [
    lanes,
    requestPatterns,
  ]);

  // Placeholder category filter — every Play lands in "All". Any
  // other category returns an empty array (acceptable per ticket;
  // P4-06 wires the real frontmatter-driven mapping).
  const visible = selectedCategory === "all" ? plays : [];

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
        <div className="mt-3">
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
        </div>
      </Card>

      {visible.length === 0 ? (
        <Card>
          <CardHeader
            title={
              selectedCategory === "all"
                ? "Catalog is empty"
                : "No plays in this category yet"
            }
            subtitle={
              selectedCategory === "all"
                ? "No plays are exposed by the backend right now. Run `shipctl sync` or check your catalog."
                : "Real category mapping ships in P4-06 — until then every play is grouped under \u201cAll\u201d. Switch back to All to see the full catalog."
            }
          />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((play) => {
            const expanded = openKey === play.id;
            const state = expanded ? cardState : { mode: "idle" as const };
            const onToggle = () => {
              setOpenKey(expanded ? null : play.id);
              setCardState({ mode: "idle" });
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

            // Lane-only play: no Run now, only Automate. ``pattern``
            // is intentionally omitted so PlayCard hides the form +
            // dispatch button.
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
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

function mergePlays(
  lanes: ApiLaneCatalogEntry[],
  requestPatterns: ApiCatalogPattern[],
): UnifiedPlay[] {
  // Index request patterns by id so we can dedupe lane recipes that
  // also expose a request mode (avoids the same Play showing twice).
  // The request-flavoured row wins because it carries dispatch
  // metadata + inputs.
  const requestById = new Map<string, ApiCatalogPattern>();
  for (const p of requestPatterns) {
    requestById.set(p.id, p);
  }

  const out: UnifiedPlay[] = [];
  for (const p of requestPatterns) {
    out.push({
      kind: "request",
      id: p.id,
      title: p.name ?? p.id,
      description: p.description || p.id,
      tags: [p.category, ...p.tags.slice(0, 2)].filter(
        (v): v is string => !!v,
      ),
      pattern: p,
    });
  }
  for (const entry of lanes) {
    // Lane catalog entries identify their pattern via ``pattern``
    // (the canonical id) or fall back to ``kind``. If we already
    // emitted the request-mode flavour, skip the lane-only echo.
    const patternId = entry.pattern ?? entry.kind;
    if (requestById.has(patternId)) continue;
    out.push({
      kind: "lane",
      id: patternId,
      title: entry.title,
      description: entry.summary,
      tags: [
        entry.event ? `event:${entry.event}` : null,
        entry.schedule ? "scheduled" : null,
      ].filter((v): v is string => !!v),
      entry,
    });
  }
  out.sort((a, b) => a.title.localeCompare(b.title));
  return out;
}
