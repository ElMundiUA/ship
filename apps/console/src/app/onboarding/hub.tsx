/**
 * OnboardingHub — the "/onboarding" landing page when at least one
 * repo on the workspace has already been seeded.
 *
 * Replaces the linear setup tunnel for already-onboarded workspaces.
 * The tunnel still kicks in for first-time setup (see ``resumeStep``
 * in ``page.tsx``); the hub takes over once the workspace has any
 * non-NULL ``installed_bundle_version``. From the hub the operator
 * can drill into any of the four sections — Code, Repositories,
 * Tracker, Bootstrap — without being forced through them in order.
 *
 * Each card carries:
 *   - title + one-sentence description
 *   - status pill (ok / action / warn) sourced from the data the
 *     server already fetched for resume detection
 *   - primary CTA pointing at ``?step=<id>`` so the existing step
 *     pages can keep handling their own state
 *
 * The workspace-defaults panel (tracker / default agent / orchestrator)
 * is rendered above the cards because those three invariants gate
 * every per-repo seed PR — surfacing them here makes "what's missing"
 * obvious without forcing the operator into the Confirm step.
 */

import Link from "next/link";

import type { ApiActivatedRepo } from "@/lib/api/client";

import {
  WorkspaceDefaultsPanel,
  type WorkspaceDefaultsPanelInitial,
} from "./workspace-defaults-panel";

export interface OnboardingHubProps {
  workspaceId: string;
  /**
   * Activated repos for the workspace. We pass the full list so the
   * cards can compute counts (``X up to date``, ``Y update available``)
   * without a second round-trip.
   */
  repos: ApiActivatedRepo[];
  githubAppInstalled: boolean;
  workspaceDefaults: WorkspaceDefaultsPanelInitial;
}

export function OnboardingHub({
  workspaceId,
  repos,
  githubAppInstalled,
  workspaceDefaults,
}: OnboardingHubProps) {
  const wsQuery = `?step=`; // section appended per-card below
  const wsSuffix = `&ws=${encodeURIComponent(workspaceId)}`;

  // Bundle drift counts. ``installed_bundle_version === null`` is
  // treated as ``not_seeded`` (greenfield); strings that don't match
  // ``current_bundle_version`` count as ``update_available``. The
  // backend stamps both fields on every ``ActivatedRepoOut``.
  const not_seeded = repos.filter((r) => r.installed_bundle_version === null);
  const update_available = repos.filter(
    (r) =>
      r.installed_bundle_version !== null &&
      r.installed_bundle_version !== r.current_bundle_version,
  );
  const up_to_date = repos.filter(
    (r) =>
      r.installed_bundle_version !== null &&
      r.installed_bundle_version === r.current_bundle_version,
  );

  const allUpToDate =
    repos.length > 0 && not_seeded.length === 0 && update_available.length === 0;

  // Tracker pill copy mirrors the workspace-defaults panel's row so
  // operators see the same answer in both places — no surprises when
  // they click through.
  const trackerLabel =
    workspaceDefaults.trackerKinds.length > 0
      ? workspaceDefaults.trackerKinds.join(", ")
      : "not connected";

  const cards: HubCard[] = [
    {
      key: "github",
      title: "Code",
      blurb:
        "GitHub App that grants Ship scoped access. Reinstall it if you've added new orgs or revoked permissions.",
      status: githubAppInstalled
        ? { tone: "ok", label: "installed" }
        : { tone: "warn", label: "not installed" },
      cta: githubAppInstalled ? "Reinstall →" : "Install →",
      href: `/onboarding${wsQuery}github${wsSuffix}`,
    },
    {
      key: "repos",
      title: "Repositories",
      blurb:
        "Repos Ship is wired into. Add another, remove one, or revisit the activation list.",
      status:
        repos.length > 0
          ? { tone: "ok", label: `${repos.length} active` }
          : { tone: "warn", label: "none picked" },
      cta: "Manage repos →",
      href: `/onboarding${wsQuery}repos${wsSuffix}`,
    },
    {
      key: "tracker",
      title: "Integrations",
      blurb:
        "Tools your team already uses — issue trackers, doc stores, repo hosts. Linear/Notion need OAuth; GitHub Issues rides on the App.",
      status:
        workspaceDefaults.trackerKinds.length > 0
          ? { tone: "ok", label: trackerLabel }
          : { tone: "warn", label: "not connected" },
      cta: workspaceDefaults.trackerKinds.length > 0 ? "Manage →" : "Connect →",
      href: `/onboarding${wsQuery}tracker${wsSuffix}`,
    },
    {
      key: "roles",
      title: "Roles",
      blurb:
        "Pick which integration plays which role: tracker, docs, default agent, code orchestrator.",
      status: rolesStatus(workspaceDefaults),
      cta: "Configure →",
      href: `/onboarding${wsQuery}roles${wsSuffix}`,
    },
    {
      key: "bootstrap",
      title: "Bootstrap state",
      blurb:
        "Per-repo Ship setup. Open a re-seed PR when a repo's version goes stale.",
      status: bootstrapStatus({
        not_seeded: not_seeded.length,
        update_available: update_available.length,
        up_to_date: up_to_date.length,
        allUpToDate,
      }),
      cta:
        not_seeded.length + update_available.length > 0
          ? "Review repos →"
          : "Review repos →",
      href: `/onboarding${wsQuery}confirm${wsSuffix}`,
    },
  ];

  return (
    <section data-testid="onboarding-hub">
      <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
        Onboarding
      </p>
      <h1 className="mt-2 font-display text-2xl font-bold leading-tight">
        Workspace setup.
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        Four sections, each editable any time. The setup tunnel runs
        you through them in order on first install; once you&apos;re
        live this page is the home base for everything onboarding-
        adjacent.
      </p>

      <div className="mt-7">
        <WorkspaceDefaultsPanel initial={workspaceDefaults} />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
        {cards.map((card) => (
          <HubCardView key={card.key} card={card} />
        ))}
      </div>

      <footer className="mt-10 flex items-center justify-between gap-3 border-t border-white/[0.08] pt-5">
        <Link
          href={`/?ws=${encodeURIComponent(workspaceId)}`}
          className="text-xs text-white/55 hover:text-white"
        >
          ← Back to dashboard
        </Link>
        <span className="text-[11px] text-white/45">
          Need to start over? Run individual sections from the cards
          above — there&apos;s no destructive reset here.
        </span>
      </footer>
    </section>
  );
}

interface HubCard {
  key: string;
  title: string;
  blurb: string;
  status: { tone: "ok" | "warn" | "neutral"; label: string };
  cta: string;
  href: string;
}

function HubCardView({ card }: { card: HubCard }) {
  const toneClass =
    card.status.tone === "ok"
      ? "border-aqua/40 bg-aqua/[0.08] text-aqua"
      : card.status.tone === "warn"
        ? "border-amber-500/40 bg-amber-500/10 text-amber-200"
        : "border-white/15 bg-white/[0.04] text-white/55";
  return (
    <Link
      href={card.href}
      className="group flex flex-col rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5 shadow-card transition hover:border-aqua/40 hover:bg-aqua/[0.04]"
      data-testid="onboarding-hub-card"
      data-card-key={card.key}
    >
      <header className="flex items-start justify-between gap-3">
        <h2 className="font-display text-lg font-bold text-white group-hover:text-aqua">
          {card.title}
        </h2>
        <span
          className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest ${toneClass}`}
        >
          {card.status.label}
        </span>
      </header>
      <p className="mt-2 flex-1 text-[12px] leading-relaxed text-white/65">
        {card.blurb}
      </p>
      <span className="mt-4 inline-flex items-center text-[11px] font-semibold uppercase tracking-widest text-white/55 group-hover:text-aqua">
        {card.cta}
      </span>
    </Link>
  );
}

function rolesStatus(d: WorkspaceDefaultsPanelInitial): {
  tone: "ok" | "warn" | "neutral";
  label: string;
} {
  const trackerSet = d.trackerKinds.length > 0;
  const agentSet = d.defaultAgentProfile !== null;
  if (trackerSet && agentSet) return { tone: "ok", label: "configured" };
  if (!trackerSet && !agentSet) return { tone: "warn", label: "needs both" };
  if (!trackerSet) return { tone: "warn", label: "tracker missing" };
  return { tone: "warn", label: "agent missing" };
}

function bootstrapStatus(counts: {
  not_seeded: number;
  update_available: number;
  up_to_date: number;
  allUpToDate: boolean;
}): { tone: "ok" | "warn" | "neutral"; label: string } {
  if (counts.allUpToDate) {
    return { tone: "ok", label: `${counts.up_to_date} up to date` };
  }
  const actionable = counts.not_seeded + counts.update_available;
  if (actionable === 0) {
    return { tone: "neutral", label: "no repos" };
  }
  return {
    tone: "warn",
    label:
      counts.not_seeded > 0 && counts.update_available > 0
        ? `${counts.not_seeded} new · ${counts.update_available} stale`
        : counts.not_seeded > 0
          ? `${counts.not_seeded} not seeded`
          : `${counts.update_available} update available`,
  };
}
