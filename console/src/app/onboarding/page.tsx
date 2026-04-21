/**
 * WOW onboarding wizard v2 — 5-step, per-repo GitHub-App driven flow.
 *
 * Architecture notes (see RFC-0007 / Wizard v2 plan):
 *
 *   1. **github**    — install the Ship GitHub App on the chosen account.
 *   2. **repos**     — pick repos from the live App install list. Activation
 *                      only; preset is chosen per-repo on the next step.
 *   3. **tracker**   — workspace-level Linear / Notion OAuth (or skip).
 *                      These credentials are shared by all repos; each
 *                      repo can still override which tracker kind (and
 *                      team/project) in step 4.
 *   4. **configure** — per-repo loop (new): preset → tracker binding →
 *                      agent GitHub Actions secrets → open seed PR.
 *                      All four sub-steps live in one page so the user
 *                      can see progress per-repo side-by-side.
 *   5. **done**      — summary of seeded PRs + "initial tasks will run
 *                      once merged" banner.
 *
 * Pre-wizard: the page redirects unauthenticated visitors straight to
 * `/login?next=/onboarding`. After login we look up (or JIT-create) the
 * user's workspace via `GET /v1/workspaces` and stick its id in the URL
 * so every step has a stable handle.
 *
 * Step transitions for 1-3 are still server-rendered native form POSTs
 * to `/api/onboard/*` route handlers (303-redirect back here). Step 4
 * is a client-driven page that calls JSON route handlers per-repo
 * (no full navigations) — this is the only way to keep secret-input
 * state and per-repo mutation isolated without reloading.
 */

import Link from "next/link";
import { redirect } from "next/navigation";

import {
  ApiHttpError,
  ApiUnavailableError,
  checkAgentSecrets,
  getRepoTrackerBinding,
  isApiConfigured,
  listActivatedRepos,
  listAvailableRepos,
  listWorkspaces,
  type ApiActivatedRepo,
  type ApiAgentSecretStatus,
  type ApiAvailableRepo,
  type ApiTrackerBinding,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { type PresetId } from "./presets";
import { RepoCard, type RepoCardInitial } from "./repo-card";

export const dynamic = "force-dynamic";

type StepId = "github" | "repos" | "tracker" | "configure" | "done";

const STEPS: { id: StepId; label: string }[] = [
  { id: "github", label: "Install GitHub App" },
  { id: "repos", label: "Pick repos" },
  { id: "tracker", label: "Workspace tracker" },
  { id: "configure", label: "Configure repos" },
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

const CONFIGURE_ERRORS: Record<string, string> = {
  load_failed:
    "Couldn't load your activated repos. Refresh; if it persists, check the backend is reachable.",
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
  return (
    v === "github" ||
    v === "repos" ||
    v === "tracker" ||
    v === "configure" ||
    // ``knowledge`` is legacy (wizard v1) — keep the pin working so
    // old email links / bookmarks still land somewhere sensible.
    v === "knowledge" ||
    v === "done"
  );
}

function pickStep(raw: string | string[] | undefined): StepId {
  const v = Array.isArray(raw) ? raw[0] : raw;
  if (
    v === "repos" ||
    v === "tracker" ||
    v === "configure" ||
    v === "done"
  ) {
    return v;
  }
  // Legacy wizard v1 routed to ``?step=knowledge`` after the tracker
  // OAuth callback. Funnel those hits into the new configure step so
  // nobody lands on a blank screen.
  if (v === "knowledge") return "configure";
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
    // Activated at least one repo → user is past the linear prefix and
    // wants to configure them. The configure step is its own landing
    // pad (per-repo cards), and tracker/step-3 is a sibling they can
    // click back to from the stepper if they want to re-do OAuth.
    if (activated.length > 0) return "configure";
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

  // Configure step: load every activated repo plus the per-repo
  // tracker binding and agent-secret status, so the RepoCard client
  // components render fully populated on first paint. We parallelise
  // the per-repo reads with ``Promise.all`` — they're independent.
  let configureCards: RepoCardInitial[] | null = null;
  let configureLoadError: string | null = null;
  if (step === "configure" && wsId && apiConfigured) {
    try {
      const activated = await listActivatedRepos(wsId, sessionToken ?? undefined);
      if (activated.length === 0) {
        // No repos → bounce to the picker. Soft redirect via URL
        // param so the banner explains what happened.
        redirect(
          `/onboarding?step=repos&ws=${encodeURIComponent(wsId)}&error=empty`,
        );
      }
      configureCards = await Promise.all(
        activated.map((r) =>
          loadRepoCardInitial(wsId, r, sessionToken ?? undefined),
        ),
      );
    } catch (err) {
      // Next's ``redirect()`` signals via a thrown sentinel — bubble it
      // up untouched or the wizard will swallow our own redirect and
      // land on a broken banner.
      if (
        err &&
        typeof err === "object" &&
        "digest" in err &&
        typeof (err as { digest: unknown }).digest === "string" &&
        (err as { digest: string }).digest.startsWith("NEXT_REDIRECT")
      ) {
        throw err;
      }
      if (err instanceof ApiHttpError && err.status === 401) {
        redirect("/login?next=%2Fonboarding");
      }
      console.error("[onboarding] configure step load failed", err);
      configureLoadError = "load_failed";
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
        {step === "configure" && wsId && (
          <ConfigureReposStep
            wsId={wsId}
            cards={configureCards}
            loadError={configureLoadError}
          />
        )}
        {step === "done" && <DoneStep wsId={wsId ?? null} />}
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
        Step 2 of 4 &middot; Pick repos
      </p>
      <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
        Which repos should Ship watch?
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        We pulled this list straight from your GitHub App installation. Tick
        the ones you want Ship to work with. You&apos;ll pick a preset and
        wire a tracker per-repo on the next-next step — no one-size-fits-all.
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
          {/*
            Wizard v2 moved preset selection to the per-repo configure
            step, but the existing ``repos-activate`` handler still accepts
            it as a bulk default. We pin ``adoption-minimum`` so any repo
            that gets activated here but never re-visited later still has
            a sane preset bound — the configure step lets the user change
            it before opening the seed PR anyway.
          */}
          <input
            type="hidden"
            name="preset"
            value="adoption-minimum"
            suppressHydrationWarning
          />

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
        Step 3 of 4 &middot; Workspace tracker (OAuth)
      </p>
      <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
        Connect your tracker.
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        This workspace-level OAuth is reused by <em>every</em> repo — next
        step lets you pick a tracker kind per repo (Linear, GitHub Issues,
        Jira) and the team/project it writes to. OAuth tokens are encrypted
        with the workspace key; the API only ever exposes{" "}
        <code className="rounded bg-white/5 px-1 py-[1px] text-aqua">
          has_secret: true
        </code>{" "}
        from here on. Skip and wire one up later from{" "}
        <Link href="/integrations" className="text-aqua underline">
          Integrations
        </Link>{" "}
        if you only want GitHub Issues.
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
// Step 4 — Configure repos (per-repo preset + tracker + secrets + seed PR)
// ---------------------------------------------------------------------------

function ConfigureReposStep({
  wsId,
  cards,
  loadError,
}: {
  wsId: string;
  cards: RepoCardInitial[] | null;
  loadError: string | null;
}) {
  const message = loadError ? CONFIGURE_ERRORS[loadError] ?? loadError : null;
  const total = cards?.length ?? 0;
  const doneHref = `/onboarding?step=done&ws=${encodeURIComponent(wsId)}`;
  return (
    <section>
      <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
        Step 4 of 4 &middot; Configure each repo
      </p>
      <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
        One PR per repo, then you&apos;re off.
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        For each repo: pick a preset, bind a tracker (or inherit the
        workspace default), paste any agent API keys we need (they go
        straight to GitHub Actions secrets — never stored on Ship), and
        open one seed PR. The PR carries the CLI, the GitHub Actions
        workflows, the scheduled lanes, a base{" "}
        <code className="rounded bg-white/5 px-1 text-aqua">.ship/config.yml</code>{" "}
        and the tracker FSM spec. Merge each PR once; the rest runs on
        its own.
      </p>

      {message && (
        <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
          {message}
        </div>
      )}

      {cards && cards.length > 0 && (
        <div className="mt-7 space-y-4">
          {cards.map((c) => (
            <RepoCard key={c.repo.id} workspaceId={wsId} initial={c} />
          ))}
        </div>
      )}

      {cards && cards.length === 0 && !loadError && (
        <div className="mt-7 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-xs text-white/70">
          No activated repos. Step back to <em>Pick repos</em> and activate at
          least one before seeding.
        </div>
      )}

      <div className="mt-8 flex items-center justify-between gap-3 border-t border-white/10 pt-5">
        <span className="text-[11px] text-white/45">
          {total > 0
            ? `${total} repo${total === 1 ? "" : "s"} ready to configure. Seed PRs don't auto-merge — you're in control.`
            : "Nothing to configure yet."}
        </span>
        <div className="flex items-center gap-3">
          <Link
            href={`/onboarding?step=repos&ws=${encodeURIComponent(wsId)}`}
            className="text-xs text-white/55 hover:text-white"
          >
            &larr; Back to repo picker
          </Link>
          <Link
            href={doneHref}
            data-testid="onboarding-configure-continue"
            className="rounded-full border border-aqua/40 bg-aqua/[0.08] px-4 py-2 text-xs font-bold text-aqua hover:bg-aqua/[0.16]"
          >
            I&apos;m done configuring &rarr;
          </Link>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Done + bootstrap-error fallbacks
// ---------------------------------------------------------------------------

function DoneStep({ wsId }: { wsId: string | null }) {
  const configureHref = wsId
    ? `/onboarding?step=configure&ws=${encodeURIComponent(wsId)}`
    : "/onboarding";
  return (
    <section className="mx-auto max-w-2xl rounded-3xl border border-aqua/30 bg-aqua/[0.04] p-10 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-aqua/20 text-2xl text-aqua">
        ✓
      </div>
      <h1
        className="mt-4 font-display text-3xl font-bold"
        data-testid="onboarding-done-title"
      >
        You&apos;re wired in.
      </h1>
      <p className="mx-auto mt-2 max-w-lg text-sm text-white/75">
        Workspace is set up, tracker is connected, and each repo has its own
        seed PR opened (with the CLI, GitHub Actions, scheduled lanes, base
        config, knowledge starters and the tracker FSM).
      </p>
      <div className="mx-auto mt-5 max-w-lg rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-left text-xs leading-relaxed text-white/80">
        <strong className="block text-aqua">What happens next:</strong>
        <ol className="mt-1 list-decimal space-y-0.5 pl-4">
          <li>
            <strong className="text-white">Merge each seed PR.</strong> That
            installs the workflows and unlocks the scheduled lanes.
          </li>
          <li>
            <strong className="text-white">Initial tasks run.</strong> On
            merge, Ship&apos;s first sweeps fire (code map, knowledge refresh,
            standup) and start populating the dashboard.
          </li>
          <li>
            <strong className="text-white">Pick up from the dashboard.</strong>{" "}
            Review, approve, tweak prompts, and let the lanes do the work.
          </li>
        </ol>
      </div>
      <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
        <Link
          href="/"
          className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
        >
          Open dashboard &rarr;
        </Link>
        <Link
          href={configureHref}
          className="rounded-full border border-white/15 bg-white/[0.04] px-5 py-2.5 text-sm font-bold text-white/85 hover:border-aqua/40 hover:text-white"
        >
          Back to repo configure
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

/**
 * Pull everything a ``RepoCard`` needs to render without another
 * round-trip on first paint: the tracker binding (even if it's
 * inherited from the workspace default) and the agent-secret catalog
 * with fresh ``present`` flags. The two calls are independent so we
 * run them in parallel.
 *
 * Resilience contract: a probe failure (e.g. the App lacks the
 * ``Secrets`` permission so GitHub returns 403 on listing secrets)
 * must **not** take down the whole step. We degrade gracefully —
 * default-shaped binding, empty agent list — and carry a short hint
 * in ``probe_errors`` so the card can render the actual remediation
 * instead of a generic "reload" banner. The full message is logged
 * server-side for ops to triage.
 */
async function loadRepoCardInitial(
  wsId: string,
  repo: ApiActivatedRepo,
  token: string | undefined,
): Promise<RepoCardInitial> {
  const [trackerRes, secretsRes] = await Promise.allSettled([
    getRepoTrackerBinding(wsId, repo.id, token),
    checkAgentSecrets(wsId, repo.id, { token }),
  ]);

  const probe_errors: { tracker?: string; agents?: string } = {};

  let tracker: ApiTrackerBinding;
  if (trackerRes.status === "fulfilled") {
    tracker = trackerRes.value;
  } else {
    const msg = formatProbeError(trackerRes.reason);
    console.error(
      `[onboarding] tracker probe failed for repo=${repo.full_name}: ${msg}`,
    );
    probe_errors.tracker = msg;
    // Neutral default so the dropdown still renders and the user can
    // bind a tracker manually. ``workspace_default_kind: null`` matches
    // what the backend returns on a truly unbound workspace.
    tracker = {
      repo_id: repo.id,
      kind: null,
      config: {},
      source: "none",
      workspace_default_kind: null,
    };
  }

  let agents: ApiAgentSecretStatus[];
  if (secretsRes.status === "fulfilled") {
    agents = secretsRes.value.agents;
  } else {
    const msg = formatProbeError(secretsRes.reason);
    console.error(
      `[onboarding] agent-secrets probe failed for repo=${repo.full_name}: ${msg}`,
    );
    probe_errors.agents = msg;
    // Empty catalog on failure — the user can still pick a preset and
    // open a seed PR. The card surfaces ``probe_errors.agents`` so
    // they know the secret check is stale.
    agents = [];
  }

  return {
    repo: {
      id: repo.id,
      full_name: repo.full_name,
      preset: (repo.preset as PresetId | null) ?? null,
      default_branch: repo.default_branch,
    },
    tracker,
    agents,
    // Wizard v2 doesn't yet persist "last seed PR" server-side; the
    // card starts out in the editable state and switches to the
    // seeded row after the user clicks the button here. Iter 8 will
    // surface pending-merge PRs on the dashboard.
    last_seed: null,
    probe_errors: Object.keys(probe_errors).length > 0 ? probe_errors : undefined,
  };
}

/**
 * Turn an arbitrary probe rejection into a short, human-safe string
 * for the UI and server logs. We keep ``ApiHttpError`` status + path
 * visible because that's the shape of nearly every real failure
 * here; for the anything-else case we fall back to ``String(err)``
 * which is conservative enough not to leak stack traces.
 */
function formatProbeError(err: unknown): string {
  if (err instanceof ApiHttpError) {
    let detail = "";
    if (typeof err.detail === "string") {
      detail = err.detail;
    } else if (err.detail != null) {
      try {
        detail = JSON.stringify(err.detail);
      } catch {
        detail = String(err.detail);
      }
    }
    return `HTTP ${err.status}${detail ? ` — ${detail.slice(0, 200)}` : ""}`;
  }
  if (err instanceof Error) return err.message;
  return String(err);
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
