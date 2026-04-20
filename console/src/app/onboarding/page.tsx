/**
 * WOW onboarding wizard (3 steps, GitHub-App driven).
 *
 * Per `documentation/internal/pilot-plan.md` we never clone customer
 * repos and we never make the user paste a URL or invent a workspace
 * name. The whole flow is:
 *
 *   1. github  — install the Ship GitHub App on the chosen account
 *   2. repos   — pick repos from the live App-installation list and
 *                let `seed_default_pipelines` materialise the lanes
 *   3. tracker — optional Linear / Notion OAuth, or skip with one click
 *
 * Pre-wizard: the page redirects unauthenticated visitors straight to
 * `/login?next=/onboarding`. After login we look up (or JIT-create) the
 * user's workspace via `GET /v1/workspaces` and stick its id in the URL
 * so every step has a stable handle.
 *
 * Step transitions are still server-rendered native form POSTs to
 * `/api/onboard/*` route handlers, which 303-redirect back here with
 * the next `step=` parameter set.
 */

import Link from "next/link";
import { redirect } from "next/navigation";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listActivatedRepos,
  listAvailableRepos,
  listWorkspaces,
  type ApiAvailableRepo,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export const dynamic = "force-dynamic";

type StepId = "github" | "repos" | "tracker" | "done";

const STEPS: { id: StepId; label: string }[] = [
  { id: "github", label: "Install GitHub App" },
  { id: "repos", label: "Pick repos" },
  { id: "tracker", label: "Connect tracker" },
];

// Must stay in lockstep with ``backend.app.services.default_pipelines.KNOWN_PRESETS``
// (the route handler also whitelists them before forwarding). Order
// here drives the picker order; ``adoption-minimum`` sits last because
// it's the "I'll wire the rest later" option.
const PRESETS: {
  id:
    | "web-app"
    | "api-backend"
    | "mobile-app"
    | "cli"
    | "monorepo"
    | "adoption-minimum";
  name: string;
  blurb: string;
  // Short list of pipelines that ship *enabled* for this preset — cosmetic.
  lanes: string;
}[] = [
  {
    id: "web-app",
    name: "Web app",
    blurb:
      "Next.js / Remix / SPA — PR review gate, daily standup, tech-debt scan, code map.",
    lanes: "PR gate · Standup · Tech-debt · Code map",
  },
  {
    id: "api-backend",
    name: "API backend",
    blurb:
      "FastAPI / Go / Rails service — identical operational baseline as web-app, tailored for server repos.",
    lanes: "PR gate · Standup · Tech-debt · Code map",
  },
  {
    id: "mobile-app",
    name: "Mobile app",
    blurb:
      "iOS / Android / RN — same four lanes; hosted E2E ships once a device-lab preset lands.",
    lanes: "PR gate · Standup · Tech-debt · Code map",
  },
  {
    id: "cli",
    name: "CLI / library",
    blurb:
      "CLI tools or libraries — quieter cadence: PR gate + tech-debt + code map only.",
    lanes: "PR gate · Tech-debt · Code map",
  },
  {
    id: "monorepo",
    name: "Monorepo",
    blurb:
      "Large multi-package repo — opts into pipeline self-heal on top of the baseline.",
    lanes: "PR gate · Standup · Tech-debt · Self-heal · Code map",
  },
  {
    id: "adoption-minimum",
    name: "Minimum",
    blurb:
      "Just the PR review gate + code map. Flip extra lanes on later from the Pipelines page.",
    lanes: "PR gate · Code map",
  },
];

const GITHUB_ERRORS: Record<string, string> = {
  api_unavailable: "Backend not reachable.",
  forbidden: "You need admin role on this workspace to install the GitHub App.",
  app_not_configured:
    "GitHub App env vars are missing on the backend (GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY / GITHUB_APP_WEBHOOK_SECRET). Ask ops to wire them up and try again.",
  bad_state:
    "Install link expired or was tampered with. Start the install again from this step.",
  unknown: "Couldn't start the install flow. Try again.",
};

const REPOS_ERRORS: Record<string, string> = {
  api_unavailable: "Backend not reachable.",
  forbidden: "You need admin role on this workspace to activate repos.",
  no_install:
    "GitHub App isn't installed on this workspace yet. Step back to the GitHub install.",
  bad_token:
    "GitHub rejected our installation token. Reinstall the Ship app and try again.",
  empty: "Pick at least one repo, or use Skip to come back later.",
  unknown: "Couldn't save the repo selection. Try again.",
};

const TRACKER_ERRORS: Record<string, string> = {
  api_unavailable: "Backend not reachable.",
  bad_kind: "Pick one of the supported trackers.",
  forbidden: "You need admin role on this workspace to add an integration.",
  not_configured:
    "OAuth credentials for that tracker aren't configured on this deployment. Ask ops to wire {LINEAR,NOTION}_CLIENT_ID/SECRET.",
  bad_state:
    "OAuth handshake failed (state expired or tampered). Start the flow again.",
  exchange_failed:
    "Tracker rejected the OAuth code. Try again, or check the application is approved by your workspace admin.",
  denied: "You declined the connection. Hit a tile to try again or skip.",
  unknown: "Something went sideways. Please retry.",
};

type SearchParams = Promise<{ [key: string]: string | string[] | undefined }>;

function pick(raw: string | string[] | undefined): string | undefined {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return v ?? undefined;
}

/** True iff the user explicitly pinned a step in the URL.
 *
 * We honour explicit pins (``?step=repos``) even when the auto-resume
 * logic would jump further ahead — that way users can click the step
 * labels in ``<Stepper/>`` to go backwards, e.g. to change their
 * preset choice without having to re-activate repos.
 */
function hasExplicitStep(raw: string | string[] | undefined): boolean {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return v === "github" || v === "repos" || v === "tracker" || v === "done";
}

function pickStep(raw: string | string[] | undefined): StepId {
  const v = Array.isArray(raw) ? raw[0] : raw;
  if (v === "repos" || v === "tracker" || v === "done") return v;
  return "github";
}

/**
 * Derive the step the operator should land on when no ``?step=`` pin is
 * present in the URL — the B8 resume pointer.
 *
 * We read state from two tiny backend calls (``/repos`` for activated
 * rows, ``/repos/available`` to detect the App install) and pick the
 * furthest step the user could meaningfully act on:
 *
 * - activated repos > 0          → ``tracker`` (they'd just re-confirm
 *                                   the repo list otherwise; the tracker
 *                                   step is the only remaining thing to
 *                                   do or skip)
 * - GitHub App installed         → ``repos``   (install is done, pick)
 * - nothing yet                  → ``github``  (fresh account)
 *
 * Any API hiccup falls back to ``github`` — the first step never errors
 * out because it doesn't hit the backend for anything except the
 * install-start handler (triggered by the user's click).
 */
async function resumeStep(
  wsId: string,
  token: string | undefined,
): Promise<StepId> {
  try {
    const activated = await listActivatedRepos(wsId, token);
    if (activated.length > 0) return "tracker";
  } catch {
    /* fall through — treat as unknown */
  }
  try {
    await listAvailableRepos(wsId, token);
    // Install exists (otherwise this 409s) but nothing activated yet.
    return "repos";
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 409) return "github";
    return "github";
  }
}

export default async function OnboardingPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const params = await searchParams;
  const requestedStep = pickStep(params.step);
  const apiConfigured = isApiConfigured();
  const sessionToken = await getSessionToken();

  // Auth gate. The wizard is meaningless without a session — we can
  // neither look up a workspace nor install a GitHub App without the
  // user being logged in. Fail loud (visible login redirect) instead of
  // showing a wizard that 401s on the first POST.
  if (apiConfigured && !sessionToken) {
    redirect("/login?next=%2Fonboarding");
  }

  // Resolve the workspace once. If the URL already carries `?ws=...`
  // we trust it (saves a list call); otherwise we fetch the session
  // user's workspaces. The backend JIT-creates a personal workspace on
  // first call so a brand-new sign-in still has somewhere to land.
  let wsId = pick(params.ws);
  if (apiConfigured && !wsId) {
    try {
      const workspaces = await listWorkspaces(sessionToken ?? undefined);
      if (workspaces.length === 0) {
        // Should be impossible — `listWorkspaces` JIT-creates on the
        // backend — but if it ever happens we surface a clean error
        // instead of crashing the wizard with `wsId === undefined`.
        return <BootstrapError reason="no_workspace" />;
      }
      wsId = workspaces[0].id;
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 401) {
        redirect("/login?next=%2Fonboarding");
      }
      const reason = err instanceof ApiUnavailableError ? "api_unreachable" : "unknown";
      return <BootstrapError reason={reason} />;
    }
  }

  // B8 — auto-resume. If the URL didn't pin a step, peek at the
  // backend to land the user on the furthest step they can make
  // progress on. This prevents the classic "closed the tab after
  // installing the App, came back, stared at the GitHub step that
  // already said 'connected'" dead-end.
  let step: StepId = requestedStep;
  if (!hasExplicitStep(params.step) && wsId && apiConfigured) {
    step = await resumeStep(wsId, sessionToken ?? undefined);
  }
  const error = pick(params.error);

  // Repo picker step: pull the live installation set once, server-side.
  // ``reposLoadError`` carries a short error code so the UI can show a
  // helpful banner without leaking backend internals.
  let availableRepos: ApiAvailableRepo[] | null = null;
  let reposLoadError: string | null = null;
  if (step === "repos" && wsId && apiConfigured) {
    try {
      availableRepos = await listAvailableRepos(wsId, sessionToken ?? undefined);
    } catch (err) {
      if (err instanceof ApiHttpError) {
        if (err.status === 409) reposLoadError = "no_install";
        else if (err.status === 502) reposLoadError = "bad_token";
        else if (err.status === 401) reposLoadError = "api_unavailable";
        else reposLoadError = "unknown";
      } else {
        reposLoadError = "unknown";
      }
    }
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-ink text-white">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(60%_60%_at_15%_15%,rgba(255,107,107,0.18),transparent),radial-gradient(50%_50%_at_85%_15%,rgba(178,118,255,0.18),transparent),radial-gradient(70%_70%_at_60%_95%,rgba(118,255,217,0.15),transparent)]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 opacity-[0.06] [background-image:linear-gradient(rgba(255,255,255,0.6)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.6)_1px,transparent_1px)] [background-size:48px_48px]"
      />

      <header className="mx-auto flex w-full max-w-5xl items-center justify-between px-6 py-6">
        <Link href="/" className="flex items-center gap-2 font-display text-lg font-bold">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-coral via-lilac to-aqua text-ink">
            S
          </span>
          Ship
        </Link>
        {sessionToken && (
          <Link href="/" className="text-xs text-white/55 hover:text-white">
            Skip to dashboard →
          </Link>
        )}
      </header>

      <main className="mx-auto w-full max-w-5xl px-6 pb-20">
        {step !== "done" && <Stepper current={step} />}

        {!apiConfigured && (
          <div className="mb-6 rounded-xl border border-coral/40 bg-coral/10 px-4 py-3 text-xs text-white/85">
            <strong className="text-coral">SHIP_API_URL is not set.</strong> The
            wizard renders but every transition will fail until the backend URL
            is configured.
          </div>
        )}

        {step === "github" && wsId && (
          <GitHubStep
            wsId={wsId}
            error={error}
            githubReason={pick(params.github)}
          />
        )}
        {step === "repos" && wsId && (
          <ReposStep
            wsId={wsId}
            error={error ?? reposLoadError ?? undefined}
            available={availableRepos}
            githubReason={pick(params.github)}
          />
        )}
        {step === "tracker" && wsId && (
          <TrackerStep
            wsId={wsId}
            error={error}
            linearStatus={pick(params.linear)}
            notionStatus={pick(params.notion)}
            reposJustWired={pick(params.repos) === "wired"}
          />
        )}
        {step === "done" && <DoneStep />}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stepper
// ---------------------------------------------------------------------------

function Stepper({ current }: { current: StepId }) {
  const idx = STEPS.findIndex((s) => s.id === current);
  return (
    <ol className="mb-10 flex flex-wrap items-center gap-x-2 gap-y-3">
      {STEPS.map((s, i) => {
        const state: "done" | "current" | "next" =
          i < idx ? "done" : i === idx ? "current" : "next";
        return (
          <li
            key={s.id}
            className="flex flex-1 min-w-[7rem] items-center gap-2"
          >
            <span
              className={
                "grid h-7 w-7 shrink-0 place-items-center rounded-full border text-[11px] font-bold " +
                (state === "current"
                  ? "border-aqua/70 bg-aqua/15 text-aqua"
                  : state === "done"
                  ? "border-aqua/40 bg-aqua/30 text-ink"
                  : "border-white/15 bg-white/[0.03] text-white/55")
              }
            >
              {state === "done" ? "✓" : i + 1}
            </span>
            <span
              className={
                "truncate text-[11px] uppercase tracking-widest " +
                (state === "current" ? "text-white" : "text-white/40")
              }
            >
              {s.label}
            </span>
            {i < STEPS.length - 1 && (
              <span
                className="hidden h-px flex-1 bg-white/10 sm:block"
                aria-hidden
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------------------
// Step 1 — GitHub App install
// ---------------------------------------------------------------------------

function GitHubStep({
  wsId,
  error,
  githubReason,
}: {
  wsId: string;
  error?: string;
  githubReason?: string;
}) {
  const message = error ? GITHUB_ERRORS[error] ?? error : null;
  // Backend redirects to `?step=repos&github=installed` after a
  // successful install; the only `github=` value we ever see on this
  // step is `request` (org-admin approval pending) or nothing at all.
  const requested = githubReason === "request";
  return (
    <section>
      <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
        Step 1 of 3 &middot; Connect your code
      </p>
      <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
        Install Ship on GitHub.
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        We use a GitHub App (not a Personal Access Token) so the
        permission is scoped to the repos you pick, the token rotates on
        its own, and you can revoke it any time from your org settings.
        After you click Install, GitHub takes you through the repo
        picker and bounces you straight back here.
      </p>

      {requested && (
        <div className="mt-5 rounded-xl border border-coral/40 bg-coral/10 px-4 py-3 text-xs text-white/85">
          <strong className="text-coral">Awaiting org-admin approval.</strong>{" "}
          Your install request was forwarded. Re-run this step once the admin
          accepts; nothing else here needs to change.
        </div>
      )}

      {message && (
        <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
          {message}
        </div>
      )}

      <form
        action="/api/onboard/github-install"
        method="POST"
        className="mt-7 flex flex-wrap items-center gap-3"
        suppressHydrationWarning
      >
        <input type="hidden" name="ws" value={wsId} suppressHydrationWarning />
        <button
          type="submit"
          data-testid="onboarding-install-github"
          className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
        >
          Install Ship on GitHub &rarr;
        </button>
      </form>

      <ul className="mt-7 grid grid-cols-1 gap-3 text-xs text-white/65 md:grid-cols-3">
        {[
          [
            "Scoped install",
            "Pick exactly the repos Ship can see. Default is selected, never all-repos.",
          ],
          [
            "Per-install tokens",
            "We mint a fresh installation token per request and cache it for ~1h.",
          ],
          [
            "Webhook-armed",
            "PR / workflow_run / installation events stream to /v1/webhooks/github.",
          ],
        ].map(([t, b]) => (
          <li
            key={t}
            className="rounded-xl border border-white/10 bg-white/[0.03] p-3"
          >
            <div className="font-semibold text-white">{t}</div>
            <div className="mt-1 text-[11px] text-white/55">{b}</div>
          </li>
        ))}
      </ul>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — Pick repos
// ---------------------------------------------------------------------------

function ReposStep({
  wsId,
  error,
  available,
  githubReason,
}: {
  wsId: string;
  error?: string;
  available: ApiAvailableRepo[] | null;
  githubReason?: string;
}) {
  const message = error ? REPOS_ERRORS[error] ?? error : null;
  const reinstallHref = `/onboarding?step=github&ws=${encodeURIComponent(wsId)}`;
  const skipHref = `/onboarding?step=tracker&ws=${encodeURIComponent(wsId)}`;
  const repos = available ?? [];
  const hasActivation = repos.some((r) => r.activated);
  const justInstalled = githubReason === "installed";

  return (
    <section>
      <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
        Step 2 of 3 &middot; Pick repos
      </p>
      <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
        Which repos should Ship watch?
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        We pulled this list straight from your GitHub App installation. Tick the
        ones we should attach the five default pipelines to. You can change this
        any time from the dashboard.
      </p>

      {justInstalled && (
        <div className="mt-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
          <strong className="text-aqua">GitHub App installed.</strong> Webhooks
          armed and per-install tokens are minted on demand.
        </div>
      )}

      {message && (
        <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
          {message}
          {(error === "no_install" || error === "bad_token") && (
            <>
              {" "}
              <Link href={reinstallHref} className="underline hover:text-white">
                Reinstall the app &rarr;
              </Link>
            </>
          )}
        </div>
      )}

      {available !== null && repos.length === 0 && (
        <div className="mt-5 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-xs text-white/70">
          The Ship app is installed but it can&apos;t see any repos yet. Open
          the install page on GitHub and grant access to at least one
          repository, then refresh.
        </div>
      )}

      {repos.length > 0 && (
        <form
          action="/api/onboard/repos-activate"
          method="POST"
          className="mt-7 space-y-4"
          suppressHydrationWarning
        >
          <input type="hidden" name="ws" value={wsId} suppressHydrationWarning />

          <fieldset className="rounded-2xl border border-white/10 bg-white/[0.025] p-3">
            <legend className="px-2 text-[11px] font-bold uppercase tracking-widest text-white/55">
              Preset — shapes the default lanes
            </legend>
            <div className="grid grid-cols-1 gap-2 p-1 md:grid-cols-2">
              {PRESETS.map((p, i) => (
                <label
                  key={p.id}
                  className="flex cursor-pointer items-start gap-3 rounded-xl border border-white/5 bg-white/[0.02] p-3 has-[:checked]:border-aqua/50 has-[:checked]:bg-aqua/[0.06]"
                >
                  <input
                    type="radio"
                    name="preset"
                    value={p.id}
                    defaultChecked={i === 0}
                    className="mt-1"
                    suppressHydrationWarning
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold text-white">
                        {p.name}
                      </span>
                      <span className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-white/45">
                        {p.id}
                      </span>
                    </div>
                    <div className="mt-1 text-[11px] leading-snug text-white/60">
                      {p.blurb}
                    </div>
                    <div className="mt-1 font-mono text-[10px] text-aqua/70">
                      {p.lanes}
                    </div>
                  </div>
                </label>
              ))}
            </div>
            <p className="mt-2 px-2 text-[11px] leading-snug text-white/45">
              Preset only picks which lanes arrive <em>enabled</em>. Every lane
              is still seeded — flip extras on later from the Pipelines page.
            </p>
          </fieldset>

          <fieldset className="space-y-2 rounded-2xl border border-white/10 bg-white/[0.025] p-3 max-h-[420px] overflow-y-auto">
            <legend className="px-2 text-[11px] font-bold uppercase tracking-widest text-white/55">
              {repos.length} visible repo{repos.length === 1 ? "" : "s"}
            </legend>
            {repos.map((r, i) => (
              <label
                key={r.external_id}
                className="flex cursor-pointer items-start gap-3 rounded-xl border border-white/5 bg-white/[0.02] p-3 has-[:checked]:border-aqua/50 has-[:checked]:bg-aqua/[0.06]"
              >
                <input
                  type="checkbox"
                  name="repo_id"
                  value={String(r.external_id)}
                  defaultChecked={r.activated || (!hasActivation && i === 0)}
                  className="mt-1"
                  suppressHydrationWarning
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-white">
                      {r.full_name}
                    </span>
                    {r.private && (
                      <span className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-white/65">
                        private
                      </span>
                    )}
                    {r.activated && (
                      <span className="rounded bg-aqua/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-aqua">
                        activated
                      </span>
                    )}
                    <span className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-white/45">
                      {r.default_branch}
                    </span>
                  </div>
                  {r.description && (
                    <div className="mt-1 line-clamp-2 text-[11px] text-white/55">
                      {r.description}
                    </div>
                  )}
                  <div className="mt-1 font-mono text-[10px] text-white/35">
                    {r.html_url}
                  </div>
                </div>
              </label>
            ))}
          </fieldset>

          <div className="flex items-center justify-between gap-3 pt-2">
            <Link
              href={skipHref}
              className="text-xs text-white/55 hover:text-white"
            >
              Skip for now &rarr;
            </Link>
            <button
              type="submit"
              data-testid="onboarding-wire-repos"
              className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
            >
              Wire selected repos &rarr;
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — Tracker (optional)
// ---------------------------------------------------------------------------

function TrackerStep({
  wsId,
  error,
  linearStatus,
  notionStatus,
  reposJustWired,
}: {
  wsId: string;
  error?: string;
  linearStatus?: string;
  notionStatus?: string;
  reposJustWired: boolean;
}) {
  const message = error ? TRACKER_ERRORS[error] ?? error : null;
  const tiles: {
    id: "linear" | "notion" | "github";
    name: string;
    blurb: string;
    tag: string;
  }[] = [
    {
      id: "linear",
      name: "Linear",
      blurb:
        "OAuth into your Linear workspace. We can list issues, transition states, and comment on tickets.",
      tag: "OAuth \u00b7 1 click",
    },
    {
      id: "notion",
      name: "Notion",
      blurb:
        "OAuth into Notion. Share at least one ticket-shaped database with the integration after approval.",
      tag: "OAuth \u00b7 1 click",
    },
    {
      id: "github",
      name: "GitHub Issues",
      blurb:
        "Reuses the GitHub App you installed earlier — no extra OAuth, no extra secret.",
      tag: "Already connected",
    },
  ];
  return (
    <section>
      <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
        Step 3 of 3 &middot; Optional
      </p>
      <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
        Pick a tracker.
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        The daily lane mirrors approved actions as tickets. OAuth tokens are
        encrypted with the workspace key — the API only ever exposes{" "}
        <code className="rounded bg-white/5 px-1 py-[1px] text-aqua">
          has_secret: true
        </code>{" "}
        from here on. You can skip this step and wire one up later from{" "}
        <Link href="/integrations" className="text-aqua underline">
          Integrations
        </Link>
        .
      </p>

      {reposJustWired && (
        <div className="mt-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
          <strong className="text-aqua">Repos wired.</strong> Default pipelines
          were seeded for the chosen preset — they&apos;ll show up on the
          dashboard as Recommended actions the moment we move past this
          step. Flip the rest on any time from the Pipelines page.
        </div>
      )}
      {linearStatus === "connected" && (
        <div className="mt-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
          <strong className="text-aqua">Linear connected.</strong> Token saved
          and ready. Pick another tracker or hit Continue below.
        </div>
      )}
      {notionStatus === "connected" && (
        <div className="mt-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
          <strong className="text-aqua">Notion connected.</strong> Remember to
          share a database with the &ldquo;Ship&rdquo; integration so we can
          read your queue.
        </div>
      )}

      {message && (
        <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
          {message}
        </div>
      )}

      <div className="mt-7 grid grid-cols-1 gap-4 md:grid-cols-3">
        {tiles.map((tile) => (
          <form
            key={tile.id}
            action="/api/onboard/tracker-install"
            method="POST"
            className="flex h-full flex-col rounded-2xl border border-white/10 bg-white/[0.03] p-4 backdrop-blur-xl shadow-card transition hover:border-aqua/40"
            suppressHydrationWarning
          >
            <input
              type="hidden"
              name="ws"
              value={wsId}
              suppressHydrationWarning
            />
            <input
              type="hidden"
              name="kind"
              value={tile.id}
              suppressHydrationWarning
            />
            <div className="flex items-center justify-between">
              <h3 className="font-display text-lg font-bold text-white">
                {tile.name}
              </h3>
              <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] uppercase tracking-widest text-white/55">
                {tile.tag}
              </span>
            </div>
            <p className="mt-2 flex-1 text-[12px] leading-relaxed text-white/65">
              {tile.blurb}
            </p>
            <button
              type="submit"
              className="mt-4 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2 text-xs font-bold text-ink shadow-glow transition hover:brightness-110"
            >
              {tile.id === "github"
                ? "Use GitHub Issues \u2192"
                : `Connect ${tile.name} \u2192`}
            </button>
          </form>
        ))}
      </div>

      <form
        action="/api/onboard/tracker-install"
        method="POST"
        className="mt-7 flex items-center justify-between gap-3 border-t border-white/10 pt-5"
        suppressHydrationWarning
      >
        <input type="hidden" name="ws" value={wsId} suppressHydrationWarning />
        <input type="hidden" name="kind" value="skip" suppressHydrationWarning />
        <span className="text-[11px] text-white/45">
          Already connected one? Just hit Continue — the wizard remembers across
          refreshes.
        </span>
        <div className="flex items-center gap-3">
          <button
            type="submit"
            className="text-xs text-white/55 hover:text-white"
            formNoValidate
          >
            Skip for now &rarr;
          </button>
          <button
            type="submit"
            data-testid="onboarding-tracker-continue"
            className="rounded-full border border-aqua/40 bg-aqua/[0.08] px-4 py-2 text-xs font-bold text-aqua hover:bg-aqua/[0.16]"
          >
            Continue &rarr;
          </button>
        </div>
      </form>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Done + bootstrap-error fallbacks
// ---------------------------------------------------------------------------

function DoneStep() {
  return (
    <section className="mx-auto max-w-xl rounded-3xl border border-aqua/30 bg-aqua/[0.04] p-10 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-aqua/20 text-2xl text-aqua">
        ✓
      </div>
      <h1
        className="mt-4 font-display text-3xl font-bold"
        data-testid="onboarding-done-title"
      >
        You&apos;re wired in.
      </h1>
      <p className="mt-2 text-sm text-white/70">
        Workspace ready, GitHub repos selected, default pipelines seeded. Open
        the dashboard and watch them light up as PRs and CI runs flow in.
      </p>
      <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
        <Link
          href="/"
          className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
        >
          Open dashboard &rarr;
        </Link>
        <Link
          href="/settings"
          className="rounded-full border border-white/15 bg-white/[0.04] px-5 py-2.5 text-sm font-bold text-white/85 hover:border-aqua/40 hover:text-white"
        >
          Mint a CLI token &rarr;
        </Link>
      </div>
    </section>
  );
}

function BootstrapError({ reason }: { reason: string }) {
  const message =
    reason === "api_unreachable"
      ? "Backend is unreachable. Set SHIP_API_URL on the console deployment and try again."
      : reason === "no_workspace"
      ? "No workspace was provisioned for your account. Reach out to ops — this should be impossible."
      : "We couldn't load your workspace. Please refresh; if the error persists, sign out and back in.";
  return (
    <div className="mx-auto mt-16 max-w-md rounded-2xl border border-coral/40 bg-coral/10 p-6 text-center text-sm text-white/85">
      <p>{message}</p>
      <Link
        href="/"
        className="mt-4 inline-block rounded-full border border-aqua/50 bg-aqua/10 px-4 py-2 text-xs font-bold text-aqua hover:bg-aqua/20"
      >
        Back to dashboard
      </Link>
    </div>
  );
}
