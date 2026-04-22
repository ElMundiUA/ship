import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import {
  Badge,
  type BadgeTone,
  Card,
  CardHeader,
  StatTile,
} from "@/components/ui";
import { repoBasePath } from "@/lib/repo-slug";
import {
  type ApiAdoptionFlag,
  type ApiAdoptionReport,
  type ApiAdoptionStage,
  ApiHttpError,
  ApiUnavailableError,
  getAdoptionReport,
  isApiConfigured,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * Fleet Adoption — workspace-unique rollup (RFC-0008 §E).
 *
 * Per-repo surfaces can see their own lanes and dispatches; only the
 * workspace can answer "how far has Ship actually landed across all
 * these repos?". The page renders a five-stage funnel
 * (installed → activated → seeded → first_run → steady) plus a repo
 * table filtered/sorted to surface friction: stuck onboardings, cold
 * lanes, suspended installs, bundles that need a rerun.
 *
 * Stage + flags come straight from the backend rollup; UI just
 * presents. The window is 14 days by default because that's the cadence
 * most scheduled lanes fire on.
 */

export const dynamic = "force-dynamic";

const STAGES: readonly {
  key: ApiAdoptionStage;
  label: string;
  hint: string;
}[] = [
  { key: "installed", label: "Installed", hint: "Workspace has the repo" },
  { key: "activated", label: "Activated", hint: "Ship explicitly turned on" },
  { key: "seeded", label: "Seeded", hint: "Wizard shipped .ship/ bundle" },
  { key: "first_run", label: "First run", hint: "At least one dispatch" },
  { key: "steady", label: "Steady", hint: "Success inside window" },
];

const FLAG_LABEL: Record<ApiAdoptionFlag, string> = {
  install_missing: "install missing",
  bundle_out_of_date: "bundle out of date",
  stuck: "stuck",
  cold: "cold",
};

const FLAG_TONE: Record<ApiAdoptionFlag, BadgeTone> = {
  install_missing: "err",
  bundle_out_of_date: "warn",
  stuck: "err",
  cold: "warn",
};

export default async function FleetAdoptionPage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="Adoption" kicker="fleet">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to wire adoption."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Ffleet%2Fadoption");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Ffleet%2Fadoption");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace = workspaces[0];

  let report: ApiAdoptionReport;
  try {
    report = await getAdoptionReport(workspace.id, { token });
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Ffleet%2Fadoption");
    }
    return renderUnavailable(err);
  }

  return (
    <AppShell title="Adoption" kicker="fleet">
      <p className="mb-5 max-w-3xl text-xs text-white/55">
        Fleet-wide rollup of where each repo is on its Ship adoption
        curve. Cumulative by design — a <em>steady</em> repo is also
        counted under every earlier stage. Window:{" "}
        <span className="font-mono">{report.window_days}d</span>.
      </p>

      <Funnel report={report} />

      <FlagRow report={report} />

      <RepoTable repos={report.repos} />
    </AppShell>
  );
}

function Funnel({ report }: { report: ApiAdoptionReport }) {
  const total = report.totals.installed;
  return (
    <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-5">
      {STAGES.map((stage) => {
        const value = report.totals[stage.key];
        const pct =
          total > 0 ? Math.round((value / total) * 100) : 0;
        return (
          <StatTile
            key={stage.key}
            label={stage.label}
            value={String(value)}
            hint={total > 0 ? `${pct}% · ${stage.hint}` : stage.hint}
          />
        );
      })}
    </div>
  );
}

function FlagRow({ report }: { report: ApiAdoptionReport }) {
  const entries: { flag: ApiAdoptionFlag; count: number }[] = [
    { flag: "stuck", count: report.totals.stuck },
    { flag: "install_missing", count: report.totals.install_missing },
    { flag: "bundle_out_of_date", count: report.totals.bundle_out_of_date },
    { flag: "cold", count: report.totals.cold },
  ];
  const nonZero = entries.filter((e) => e.count > 0);
  if (nonZero.length === 0) return null;
  return (
    <Card className="mb-6">
      <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
        Needs attention
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {nonZero.map(({ flag, count }) => (
          <div key={flag} className="flex items-center gap-2">
            <Badge tone={FLAG_TONE[flag]} dot>
              {FLAG_LABEL[flag]}
            </Badge>
            <span className="font-mono text-xs text-white/70">
              {count}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function RepoTable({
  repos,
}: {
  repos: ApiAdoptionReport["repos"];
}) {
  if (repos.length === 0) {
    return (
      <Card>
        <CardHeader
          title="No repos activated yet"
          subtitle="Run the onboarding wizard to activate your first repo."
        />
      </Card>
    );
  }

  // Sort so the interesting rows float up: stuck/cold/install_missing
  // first, then by stage ascending (so the least-adopted repos lead).
  const stageOrder: Record<ApiAdoptionStage, number> = {
    installed: 0,
    activated: 1,
    seeded: 2,
    first_run: 3,
    steady: 4,
  };
  const sorted = [...repos].sort((a, b) => {
    const aAttention = a.flags.length > 0 ? 0 : 1;
    const bAttention = b.flags.length > 0 ? 0 : 1;
    if (aAttention !== bAttention) return aAttention - bAttention;
    const stageDelta = stageOrder[a.stage] - stageOrder[b.stage];
    if (stageDelta !== 0) return stageDelta;
    return a.full_name.localeCompare(b.full_name);
  });

  return (
    <Card padded={false}>
      <div className="border-b border-white/[0.08] px-5 py-3 text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
        Repos ({repos.length})
      </div>
      <ul className="divide-y divide-white/[0.06]">
        {sorted.map((r) => (
          <li key={r.repo_id}>
            <Link
              href={repoBasePath({ full_name: r.full_name })}
              className="block px-5 py-3 transition hover:bg-white/[0.03]"
            >
              <div className="flex flex-wrap items-center gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate font-mono text-sm text-white">
                      {r.full_name}
                    </span>
                    <Badge tone={stageTone(r.stage)} dot>
                      {stageLabel(r.stage)}
                    </Badge>
                    {r.preset ? (
                      <span className="text-[11px] text-white/45">
                        {r.preset}
                      </span>
                    ) : null}
                  </div>
                  {r.flags.length > 0 ? (
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      {r.flags.map((f) => (
                        <Badge key={f} tone={FLAG_TONE[f]}>
                          {FLAG_LABEL[f]}
                        </Badge>
                      ))}
                    </div>
                  ) : null}
                </div>
                <div className="hidden min-w-[180px] flex-col items-end text-right text-[11px] text-white/55 md:flex">
                  <span className="font-mono text-white/75">
                    {r.runs_in_window} runs ·{" "}
                    {formatRate(r.success_rate_in_window)}
                  </span>
                  <span>last: {formatRelative(r.last_run_at)}</span>
                  {r.installed_bundle_version !== null ? (
                    <span>
                      bundle v{r.installed_bundle_version}
                      {r.installed_bundle_version <
                      r.current_bundle_version
                        ? ` → v${r.current_bundle_version}`
                        : ""}
                    </span>
                  ) : null}
                </div>
                <span className="text-white/30">→</span>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function stageTone(stage: ApiAdoptionStage): BadgeTone {
  switch (stage) {
    case "steady":
      return "ok";
    case "first_run":
      return "info";
    case "seeded":
      return "workspace";
    case "activated":
      return "warn";
    case "installed":
      return "neutral";
  }
}

function stageLabel(stage: ApiAdoptionStage): string {
  switch (stage) {
    case "first_run":
      return "first run";
    default:
      return stage;
  }
}

function formatRate(rate: number | null): string {
  if (rate === null) return "no activity";
  return `${Math.round(rate * 100)}% success`;
}

function formatRelative(iso: string | null): string {
  if (!iso) return "never";
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return iso;
  const sec = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.round(hr / 24);
  return `${days}d ago`;
}

function renderUnavailable(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="Adoption" kicker="fleet">
      <Card>
        <CardHeader
          title="Couldn't load adoption report"
          subtitle={
            isUnavailable
              ? "Backend is unreachable. Try again in a few seconds."
              : "Something went wrong."
          }
        />
      </Card>
    </AppShell>
  );
}
