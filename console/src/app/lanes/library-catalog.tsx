"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge, Card, CardHeader } from "@/components/ui";
import type {
  ApiActivatedRepo,
  ApiLane,
  ApiLaneCatalogEntry,
  ApiRepoConfig,
} from "@/lib/api/client";

import {
  buildBaseline,
  submitProposal,
  type FanoutMode,
  type LaneDraft,
} from "./config-draft";
import { specToCron } from "./cron";
import { CustomLaneAuthor } from "./custom-author";
import { FanoutPicker } from "./fanout-picker";
import { ScheduleWizard, specFromCron } from "./schedule-wizard";

/**
 * Library tab — catalog of available lane recipes.
 *
 * Replaces the old ``library-editor`` table with a card grid: each
 * built-in recipe gets its own card that can be toggled open to add
 * it (picking a schedule via the wizard) or, if it's already in the
 * repo's ``.ship/config.yml``, to edit the schedule or remove it.
 *
 * Each Save opens **one PR per change** — keeping the flow single-
 * card means the diff in the PR is always exactly one lane, which
 * matches how users think about the action ("добавил таску / сменил
 * расписание").
 *
 * At the end of the grid, a dedicated "+ Author custom lane" card
 * expands the existing ``CustomLaneAuthor`` form so the New tab can
 * retire without losing that surface.
 *
 * The component also supports an ``initialOpenKind`` prop (driven by
 * a ``?open=<kind>`` search param) so the Active tab's "Edit
 * schedule" button can deep-link into the right card.
 */

type CardState =
  // Default: not expanded. Shows summary + Add / Edit / Remove buttons.
  | { mode: "idle" }
  // User is editing/adding — wizard is visible.
  | { mode: "edit" }
  // Propose in flight.
  | { mode: "saving" }
  // Error / drift banner.
  | { mode: "error"; message: string; code?: string };

const CUSTOM_AUTHOR_KEY = "__custom__";

export function LibraryCatalog({
  workspaceId,
  selectedRepo,
  repos,
  catalog,
  lanes,
  config,
  configError,
  initialOpenKind,
}: {
  workspaceId: string;
  selectedRepo: ApiActivatedRepo | null;
  repos: ApiActivatedRepo[];
  catalog: ApiLaneCatalogEntry[];
  lanes: ApiLane[];
  config: ApiRepoConfig | null;
  configError: string | null;
  initialOpenKind?: string | null;
}) {
  const baseline = useMemo(
    () => buildBaseline(catalog, config, lanes),
    [catalog, config, lanes],
  );

  // Track which card is open (one at a time keeps the surface
  // focused; opening another card collapses the previous).
  const [openKey, setOpenKey] = useState<string | null>(
    initialOpenKind ?? null,
  );
  const [cardState, setCardState] = useState<CardState>({ mode: "idle" });

  // A deep-link from Active (?open=daily_standup) lands us pre-
  // expanded in edit mode if the lane already exists.
  const initialEdit = useMemo(() => {
    if (!initialOpenKind) return null;
    return baseline[initialOpenKind]?.enabled ? "edit" : "edit";
  }, [initialOpenKind, baseline]);

  function openCard(laneId: string) {
    setOpenKey(laneId);
    setCardState({ mode: initialEdit && laneId === initialOpenKind ? "edit" : "edit" });
  }
  function closeCard() {
    setOpenKey(null);
    setCardState({ mode: "idle" });
  }

  if (catalog.length === 0) {
    return (
      <Card>
        <CardHeader
          title="Library is empty"
          subtitle="No built-in lane recipes are exposed by the backend yet."
        />
      </Card>
    );
  }

  if (!selectedRepo) {
    return (
      <Card>
        <CardHeader
          title="Activate a repo first"
          subtitle="The Library writes to .ship/config.yml on a specific repo. Activate one via onboarding to enable this tab."
        />
        <div className="mt-3">
          <Link
            href="/onboarding?step=github"
            className="inline-flex rounded border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua hover:bg-aqua/20"
          >
            Open onboarding →
          </Link>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {repos.length > 1 ? (
        <RepoSwitcher repos={repos} selectedRepo={selectedRepo} />
      ) : null}

      {configError ? (
        <Card className="border-coral/25 bg-coral/5">
          <p className="text-xs text-coral">{configError}</p>
        </Card>
      ) : null}

      {config && config.exists === false ? (
        <Card className="border-sun/20 bg-sun/5">
          <p className="text-xs text-white/75">
            <span className="font-semibold text-sun">No config yet.</span> Adding
            your first lane will create{" "}
            <code className="rounded bg-white/[0.06] px-1 py-0.5">
              .ship/config.yml
            </code>{" "}
            from scratch via a fresh PR.
          </p>
        </Card>
      ) : null}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {catalog.map((entry) => (
          <RecipeCard
            key={entry.kind}
            entry={entry}
            baseline={baseline[entry.kind]}
            allBaseline={baseline}
            workspaceId={workspaceId}
            repoId={selectedRepo.id}
            baseSha={config?.sha ?? null}
            catalog={catalog}
            open={openKey === entry.kind}
            state={openKey === entry.kind ? cardState : { mode: "idle" }}
            onOpen={() => openCard(entry.kind)}
            onClose={closeCard}
            onStateChange={setCardState}
          />
        ))}

        <CustomAuthorCard
          open={openKey === CUSTOM_AUTHOR_KEY}
          onOpen={() => openCard(CUSTOM_AUTHOR_KEY)}
          onClose={closeCard}
          workspaceId={workspaceId}
          selectedRepo={selectedRepo}
          repos={repos}
          config={config}
        />
      </div>

      <ProposeToPublic />
    </div>
  );
}

// ----------------------------------------------------------------------------
// Recipe card
// ----------------------------------------------------------------------------

function RecipeCard({
  entry,
  baseline,
  allBaseline,
  workspaceId,
  repoId,
  baseSha,
  catalog,
  open,
  state,
  onOpen,
  onClose,
  onStateChange,
}: {
  entry: ApiLaneCatalogEntry;
  baseline: LaneDraft;
  allBaseline: Record<string, LaneDraft>;
  workspaceId: string;
  repoId: string;
  baseSha: string | null;
  catalog: ApiLaneCatalogEntry[];
  open: boolean;
  state: CardState;
  onOpen: () => void;
  onClose: () => void;
  onStateChange: (s: CardState) => void;
}) {
  const isScheduled = entry.schedule !== null && entry.schedule !== undefined;
  const added = baseline.enabled;
  // Wizard spec — seed from the baseline schedule when editing an
  // existing lane, or from the recipe default when adding fresh.
  const [spec, setSpec] = useState(() =>
    specFromCron(baseline.schedule ?? entry.schedule ?? null),
  );
  // Fan-out state only matters for ≥2-pattern lanes. Seed from
  // baseline so editing a lane that already picked ``sequential``
  // doesn't silently reset it back to ``matrix``.
  const [fanout, setFanout] = useState<FanoutMode>(baseline.fanout);

  async function save(nextDraft: Record<string, LaneDraft>, summary: string) {
    onStateChange({ mode: "saving" });
    const result = await submitProposal({
      workspaceId,
      repoId,
      baseSha,
      catalog,
      draft: nextDraft,
      changeSummary: summary,
    });
    if (!result.ok) {
      onStateChange({
        mode: "error",
        message: result.error,
        code: result.code,
      });
      return;
    }
    window.location.href = result.pr_url;
  }

  async function handleAddOrUpdate() {
    const cron = specToCron(spec);
    const nextDraft: Record<string, LaneDraft> = {
      ...allBaseline,
      [entry.kind]: {
        ...baseline,
        enabled: true,
        schedule: cron,
        fanout,
      },
    };
    const verb = added ? "Update" : "Add";
    await save(nextDraft, `${verb} lane ${entry.kind}`);
  }

  async function handleRemove() {
    const nextDraft: Record<string, LaneDraft> = {
      ...allBaseline,
      [entry.kind]: { ...baseline, enabled: false },
    };
    await save(nextDraft, `Remove lane ${entry.kind}`);
  }

  return (
    <Card
      className={
        "flex flex-col " + (open ? "border-aqua/40 bg-aqua/[0.04]" : "")
      }
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={triggerTone(entry)}>{triggerLabel(entry)}</Badge>
            {added ? <Badge tone="ok">added</Badge> : null}
          </div>
          <h3 className="mt-1.5 font-display text-sm font-bold text-white">
            {entry.title}
          </h3>
          <p className="mt-0.5 font-mono text-[10px] text-white/45">
            {entry.kind}
          </p>
        </div>
      </div>

      <p className="mt-2 min-h-[32px] text-[12px] text-white/65">
        {entry.summary}
      </p>

      {!open ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {isScheduled ? (
            <button
              type="button"
              onClick={onOpen}
              className="rounded-md border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-semibold text-aqua hover:bg-aqua/20"
            >
              {added ? "Edit schedule" : "Add to calendar"}
            </button>
          ) : (
            <button
              type="button"
              onClick={onOpen}
              className="rounded-md border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-semibold text-aqua hover:bg-aqua/20"
              disabled={added}
            >
              {added ? "Already added" : "Add to config"}
            </button>
          )}
          {added ? (
            <button
              type="button"
              onClick={handleRemove}
              className="rounded-md border border-white/15 bg-white/[0.04] px-3 py-1 text-[11px] font-semibold text-white/70 hover:border-coral/40 hover:text-coral"
            >
              Remove
            </button>
          ) : null}
        </div>
      ) : (
        <div className="mt-3 space-y-4 border-t border-white/10 pt-3">
          {isScheduled ? (
            <ScheduleWizard spec={spec} onChange={setSpec} />
          ) : (
            <p className="rounded-md border border-white/10 bg-white/[0.02] px-3 py-2 text-[11px] text-white/55">
              This recipe fires on{" "}
              <code className="font-mono">{entry.event ?? "manual"}</code>{" "}
              events — no schedule to pick. Saving will just enable it
              in your config.
            </p>
          )}

          <FanoutPicker
            patterns={baseline.patterns}
            value={fanout}
            onChange={setFanout}
          />

          {state.mode === "error" ? (
            <div className="rounded-md border border-coral/40 bg-coral/10 px-3 py-2 text-[11px] text-coral">
              {state.message}
              {state.code === "sha_mismatch" ? (
                <>
                  {" "}
                  Reload the page to pick up the newer{" "}
                  <code>.ship/config.yml</code>.
                </>
              ) : null}
            </div>
          ) : null}

          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setSpec(specFromCron(baseline.schedule ?? entry.schedule ?? null));
                setFanout(baseline.fanout);
                onClose();
              }}
              className="rounded-md border border-white/15 bg-white/[0.04] px-3 py-1 text-[11px] font-semibold text-white/70 hover:text-white"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleAddOrUpdate}
              disabled={state.mode === "saving"}
              className="rounded-md border border-aqua/50 bg-aqua/15 px-4 py-1 text-[11px] font-semibold text-aqua hover:bg-aqua/25 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {state.mode === "saving"
                ? "Opening PR…"
                : added
                  ? "Save → PR"
                  : "Add → PR"}
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}

// ----------------------------------------------------------------------------
// Custom-author card (replaces the retired /lanes?tab=new)
// ----------------------------------------------------------------------------

function CustomAuthorCard({
  open,
  onOpen,
  onClose,
  workspaceId,
  selectedRepo,
  repos,
  config,
}: {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  workspaceId: string;
  selectedRepo: ApiActivatedRepo;
  repos: ApiActivatedRepo[];
  config: ApiRepoConfig | null;
}) {
  return (
    <Card
      className={
        "flex flex-col border-dashed " +
        (open ? "border-aqua/40 bg-aqua/[0.04]" : "border-white/15")
      }
    >
      <div>
        <Badge tone="info">custom</Badge>
        <h3 className="mt-1.5 font-display text-sm font-bold text-white">
          + Author custom lane
        </h3>
        <p className="mt-0.5 font-mono text-[10px] text-white/45">
          workflow YAML + prompt in one PR
        </p>
      </div>
      <p className="mt-2 min-h-[32px] text-[12px] text-white/65">
        Spin up a lane that isn&apos;t in the catalog — pick an agent,
        write a prompt, choose a schedule. We emit the workflow YAML,
        the prompt file, and the <code>lanes:</code> entry in a single
        PR.
      </p>
      {!open ? (
        <div className="mt-3">
          <button
            type="button"
            onClick={onOpen}
            className="rounded-md border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-semibold text-aqua hover:bg-aqua/20"
          >
            Author
          </button>
        </div>
      ) : (
        <div className="mt-3 border-t border-white/10 pt-3">
          <CustomLaneAuthor
            workspaceId={workspaceId}
            selectedRepo={selectedRepo}
            repos={repos}
            config={config}
          />
          <button
            type="button"
            onClick={onClose}
            className="mt-2 text-[11px] text-white/55 hover:text-white"
          >
            ← Close author
          </button>
        </div>
      )}
    </Card>
  );
}

// ----------------------------------------------------------------------------
// Propose-to-public-library placeholder (Phase 3 coming-soon)
// ----------------------------------------------------------------------------

function ProposeToPublic() {
  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px] text-white/45">
      Authored something great?
      <button
        type="button"
        onClick={() =>
          alert(
            "Coming soon — we'll let you propose your lane to the public Ship library via a fork + upstream PR.",
          )
        }
        className="rounded-full border border-white/15 bg-white/[0.04] px-2.5 py-0.5 text-[10px] font-semibold text-white/70 hover:border-white/30 hover:text-white"
      >
        Propose to public library
      </button>
      <span className="text-white/35">(coming soon)</span>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

function RepoSwitcher({
  repos,
  selectedRepo,
}: {
  repos: ApiActivatedRepo[];
  selectedRepo: ApiActivatedRepo;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-white/55">
      <span className="font-semibold">Repo:</span>
      {repos.map((r) => (
        <Link
          key={r.id}
          href={`/lanes?tab=library&repo_id=${encodeURIComponent(r.id)}`}
          className={
            "rounded-full border px-2.5 py-1 font-mono text-[11px] transition " +
            (r.id === selectedRepo.id
              ? "border-aqua/40 bg-aqua/10 text-aqua"
              : "border-white/10 bg-white/[0.04] text-white/70 hover:text-white")
          }
        >
          {r.full_name}
        </Link>
      ))}
    </div>
  );
}

function triggerTone(
  entry: ApiLaneCatalogEntry,
): "neutral" | "workspace" | "project" {
  if (entry.schedule) return "workspace";
  if (entry.event) return "project";
  return "neutral";
}

function triggerLabel(entry: ApiLaneCatalogEntry): string {
  if (entry.schedule) return "scheduled";
  if (entry.event === "pull_request") return "PR";
  if (entry.event === "push") return "push";
  return entry.event ?? "manual";
}
