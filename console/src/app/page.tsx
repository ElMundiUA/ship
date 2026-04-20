import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { DashboardLive } from "@/components/dashboard-live";
import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
  StatTile,
} from "@/components/ui";
import {
  type ApiActivatedRepo,
  type ApiDashboard,
  ApiHttpError,
  ApiUnavailableError,
  getDashboard,
  isApiConfigured,
  listActivatedRepos,
  listWorkspaces,
} from "@/lib/api/client";
import type { ApiWorkspace } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";
import {
  actionItems,
  kpis,
  recentRuns,
  relativeTime,
  workspaces,
  yesterdayDigest,
} from "@/lib/mock/cloud";

const ws = workspaces[0];

export const dynamic = "force-dynamic";

type SearchParams = { [key: string]: string | string[] | undefined };

export default async function CloudHomePage({
  searchParams,
}: {
  searchParams?: Promise<SearchParams>;
}) {
  const params = (await searchParams) ?? {};

  // Real cloud deployment: SHIP_API_URL is set, so the root must always
  // resolve to either the live dashboard or the auth flow — never the
  // mock fixtures. The mock dashboard is kept ONLY for marketing-preview
  // deployments where the operator deliberately leaves SHIP_API_URL unset
  // (so /, /login, /onboarding render with shipped copy + no backend).
  if (isApiConfigured()) {
    const token = await getSessionToken();
    if (!token) redirect("/login?next=%2F");
    const result = await loadLiveContext(token);
    if (result === "unauthorized") redirect("/login?next=%2F");
    if (result === "empty") redirect("/onboarding?step=github");
    if (result === "down") return renderDownState();
    // We deliberately do NOT bounce users with active_repos === 0 back
    // into the wizard. They might have skipped setup on purpose, or come
    // back after revoking the App, or just want to read the docs. The
    // dashboard surfaces a "finish setup" callout for that case
    // (see DashboardLive), but the page itself stays accessible.
    return renderLiveDashboard(result, params);
  }

  return renderMockDashboard();
}

type LiveContext = {
  workspace: ApiWorkspace;
  data: ApiDashboard;
  repos: ApiActivatedRepo[];
};

async function loadLiveContext(
  token: string,
): Promise<LiveContext | "empty" | "unauthorized" | "down"> {
  let list: ApiWorkspace[];
  try {
    list = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
  if (list.length === 0) return "empty";

  const workspace = list[0];
  try {
    // Repos load is best-effort — a 5xx here shouldn't blank the
    // dashboard, the chip just falls back to "no repo bound".
    const [data, repos] = await Promise.all([
      getDashboard(workspace.id, token),
      listActivatedRepos(workspace.id, token).catch(() => [] as ApiActivatedRepo[]),
    ]);
    return { workspace, data, repos };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
}

function renderLiveDashboard(ctx: LiveContext, params: SearchParams) {
  const { workspace, data, repos } = ctx;
  const banner = pickBanner(params);
  // Pick the lexicographically-smallest repo as the default scope so
  // that the sidebar matches what ``activate_repos`` chose as the
  // pipelines' default binding (see backend/.../repos.py).
  const sortedRepos = [...repos].sort((a, b) =>
    a.full_name.localeCompare(b.full_name),
  );
  const selectedRepo = sortedRepos[0];
  return (
    <AppShell
      title="Operating dashboard"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      scope={{
        repos: sortedRepos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: selectedRepo?.id ?? null,
      }}
      actions={
        <>
          <Link
            href="/settings"
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            CLI tokens
          </Link>
          <Link
            href="/integrations"
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            Integrations
          </Link>
          <ButtonPrimary>
            <Link
              href={`/onboarding?step=repos&ws=${encodeURIComponent(workspace.id)}`}
            >
              Pick more repos →
            </Link>
          </ButtonPrimary>
        </>
      }
    >
      <DashboardLive
        workspaceId={workspace.id}
        workspaceName={workspace.name}
        workspaceSlug={workspace.slug}
        data={data}
        banner={banner}
      />
    </AppShell>
  );
}

function pickBanner(
  params: SearchParams,
): { kind: string; reason: string; detail?: string } | undefined {
  // Either ?ran=<id>&reason=<code> or ?toggled=<id>&reason=<code> (set
  // by the dashboard form handlers). We don't bother validating the
  // pipeline id — the banner copy doesn't depend on it. Upstream
  // errors (GitHub said no) carry a truncated ``detail`` so the
  // operator sees the actual HTTP status + message body excerpt
  // instead of a generic "check perms" copy.
  const reasonRaw = params.reason;
  if (!reasonRaw) return undefined;
  const reason = Array.isArray(reasonRaw) ? reasonRaw[0] : reasonRaw;
  const detailRaw = params.detail;
  const detail = Array.isArray(detailRaw) ? detailRaw[0] : detailRaw;
  if (params.ran) return { kind: "Run", reason, detail };
  if (params.toggled) return { kind: "Toggle", reason, detail };
  if (params.installed) return { kind: "Install", reason, detail };
  return undefined;
}

function renderDownState() {
  return (
    <AppShell title="Operating dashboard">
      <Card>
        <CardHeader
          title="Backend unreachable"
          subtitle="The dashboard couldn't load live data."
        />
        <p className="text-sm text-white/70">
          Try again in a few seconds. If this keeps happening, check the
          backend service in your hosting console.
        </p>
      </Card>
    </AppShell>
  );
}

function renderMockDashboard() {
  const proposed = actionItems.filter((a) => a.status === "proposed");
  const approved = actionItems.filter((a) => a.status === "approved");

  return (
    <AppShell
      kicker={ws.org}
      title="Operating dashboard"
      actions={
        <>
          <ButtonGhost>Export digest</ButtonGhost>
          <ButtonPrimary>+ Trigger lane</ButtonPrimary>
        </>
      }
    >
      <MockBanner />

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((k) => (
          <StatTile key={k.label} {...k} />
        ))}
      </section>

      <section className="mt-8 grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* Yesterday's digest */}
        <Card className="lg:col-span-2">
          <CardHeader
            title="Yesterday's digest"
            subtitle={`Generated by daily lane · ${yesterdayDigest.date}`}
            action={
              <Link
                href="/daily"
                className="text-xs font-semibold text-aqua hover:underline"
              >
                Open →
              </Link>
            }
          />
          <p className="text-sm leading-relaxed text-white/80">{yesterdayDigest.summary}</p>
          <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-3">
            <DigestColumn title="Shipped" tone="ok" items={yesterdayDigest.shipped} />
            <DigestColumn title="In flight" tone="info" items={yesterdayDigest.inFlight} />
            <DigestColumn title="Blockers" tone="err" items={yesterdayDigest.blockers} />
          </div>
        </Card>

        {/* Proposed action items */}
        <Card>
          <CardHeader
            title="Action items waiting"
            subtitle={`${proposed.length} proposed · ${approved.length} approved this week`}
            action={
              <Link
                href="/daily"
                className="text-xs font-semibold text-aqua hover:underline"
              >
                Triage →
              </Link>
            }
          />
          <ul className="space-y-3">
            {proposed.map((a) => (
              <li
                key={a.id}
                className="rounded-xl border border-white/10 bg-white/[0.03] p-3 transition hover:border-white/20"
              >
                <div className="flex items-start gap-2">
                  <Badge
                    tone={
                      a.severity === "high" ? "err" : a.severity === "med" ? "warn" : "info"
                    }
                  >
                    {a.severity}
                  </Badge>
                  <span className="text-[10px] uppercase tracking-widest text-white/40">
                    {a.source}
                  </span>
                </div>
                <h4 className="mt-1.5 text-sm font-semibold text-white">{a.title}</h4>
                <p className="mt-1 line-clamp-2 text-xs text-white/55">{a.reason}</p>
                <div className="mt-2 flex items-center gap-2">
                  <button className="rounded-full bg-emerald-400/15 px-2.5 py-1 text-[11px] font-bold text-emerald-300 hover:bg-emerald-400/25">
                    Approve → tracker
                  </button>
                  <button className="rounded-full border border-white/15 px-2.5 py-1 text-[11px] font-semibold text-white/65 hover:bg-white/[0.06] hover:text-white">
                    Reject
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      </section>

      <section className="mt-8 grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Recent lane runs"
            subtitle="Daily, retro, scheduled and self-heal lanes — last 24 hours"
            action={
              <Link
                href="/workflows"
                className="text-xs font-semibold text-aqua hover:underline"
              >
                All runs →
              </Link>
            }
          />
          <div className="overflow-hidden rounded-xl border border-white/10">
            <table className="min-w-full text-sm">
              <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
                <tr>
                  <th className="px-3 py-2 text-left font-semibold">Lane</th>
                  <th className="px-3 py-2 text-left font-semibold">Status</th>
                  <th className="px-3 py-2 text-left font-semibold">When</th>
                  <th className="px-3 py-2 text-left font-semibold">Highlight</th>
                </tr>
              </thead>
              <tbody>
                {recentRuns.map((r) => (
                  <tr key={r.id} className="border-t border-white/5 hover:bg-white/[0.02]">
                    <td className="px-3 py-2.5 align-top">
                      <div className="font-semibold capitalize text-white">{r.kind}</div>
                      <div className="text-[10px] text-white/40">{r.trigger}</div>
                    </td>
                    <td className="px-3 py-2.5 align-top">
                      <Badge
                        tone={
                          r.status === "ok"
                            ? "ok"
                            : r.status === "warning"
                              ? "warn"
                              : "err"
                        }
                        dot
                      >
                        {r.status} · {Math.round(r.durationSec)}s
                      </Badge>
                    </td>
                    <td className="px-3 py-2.5 align-top text-xs text-white/55">
                      {relativeTime(r.startedAt)}
                    </td>
                    <td className="px-3 py-2.5 align-top text-xs text-white/75">
                      {r.highlight}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <CardHeader title="Catalog sources" subtitle="Toggled per workspace" />
          <ul className="space-y-3">
            {(["global", "workspace", "project"] as const).map((src) => (
              <li
                key={src}
                className="flex items-center justify-between gap-3 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2.5"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Badge
                      tone={src === "global" ? "neutral" : src === "workspace" ? "workspace" : "project"}
                    >
                      {src}
                    </Badge>
                  </div>
                  <p className="mt-1 text-[11px] leading-snug text-white/55">
                    {src === "global"
                      ? "Read-only mirror of the public Ship monorepo."
                      : src === "workspace"
                        ? "Helio · Platform team's private artifact repo."
                        : "Project-pinned overrides (e.g. Helio Payments)."}
                  </p>
                </div>
                <FakeToggle on={ws.catalogSources[src]} />
              </li>
            ))}
          </ul>
          <Link
            href="/settings"
            className="mt-4 block text-center text-xs font-semibold text-aqua hover:underline"
          >
            Manage in workspace settings →
          </Link>
        </Card>
      </section>
    </AppShell>
  );
}

function DigestColumn({
  title,
  tone,
  items,
}: {
  title: string;
  tone: "ok" | "info" | "err";
  items: string[];
}) {
  return (
    <div>
      <Badge tone={tone}>{title}</Badge>
      <ul className="mt-2 space-y-1.5">
        {items.map((item) => (
          <li
            key={item}
            className="flex gap-2 text-xs leading-snug text-white/75 before:content-['·'] before:text-white/30"
          >
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function FakeToggle({ on }: { on: boolean }) {
  return (
    <span
      aria-hidden
      className={
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition " +
        (on ? "bg-aqua/70" : "bg-white/10")
      }
    >
      <span
        className={
          "inline-block h-4 w-4 transform rounded-full bg-white shadow transition " +
          (on ? "translate-x-4" : "translate-x-0.5")
        }
      />
    </span>
  );
}
