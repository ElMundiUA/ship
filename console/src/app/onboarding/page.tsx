/**
 * WOW onboarding wizard — 5-step, per-repo GitHub-App driven flow.
 *
 * Wave-8c collapses the 14-preset configure step into a single
 * "Confirm bootstrap" preview that surfaces what the new
 * ``wizard_seed v2`` actually does (canonical Plays bundle,
 * CODEOWNERS-derived Inbox routing rules, repo-intel harvest).
 *
 * Steps:
 *
 *   1. **github**   — install the Ship GitHub App on the chosen account.
 *   2. **repos**    — pick repos from the live App install list.
 *   3. **tracker**  — workspace-level Linear / Notion OAuth (or skip).
 *   4. **confirm**  — per-repo "what will land" preview + one-click
 *                     "Open seed PR" CTA. Replaces the old
 *                     ``configure`` step (which carried the 14-preset
 *                     radio); ``?step=configure`` URLs in the wild
 *                     303-redirect to ``?step=confirm`` for back-compat.
 *   5. **done**     — Wave-8c "what just happened" summary owned by
 *                     P5-09 (codeowners + intel + synthetic lane stats
 *                     read from sessionStorage handed off by the
 *                     confirm step).
 *
 * Pre-wizard: unauthenticated visitors redirect to
 * ``/login?next=/onboarding``; after login we look up (or JIT-create)
 * the workspace via ``GET /v1/workspaces`` and stick its id in the URL
 * so every step has a stable handle.
 *
 * Step transitions for 1-3 are server-rendered native form POSTs to
 * ``/api/onboard/*`` route handlers (303-redirect back here). The
 * confirm step is a server-rendered preview with one client-side CTA
 * per repo that POSTs to ``/api/onboard/wizard-seed``.
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

import { ConfirmStep } from "./confirm-step";
import { DoneStep } from "./done-step";
import { type RepoCardInitial } from "./repo-card";

export const dynamic = "force-dynamic";

type StepId = "github" | "repos" | "tracker" | "confirm" | "done";

const STEPS: { id: StepId; label: string }[] = [
  { id: "github", label: "Install GitHub App" },
  { id: "repos", label: "Pick repos" },
  { id: "tracker", label: "Workspace tracker" },
  { id: "confirm", label: "Confirm" },
];


const GITHUB_ERRORS: Record<string, string> = {
  api_unavailable: "Backend not reachable.",
  workspace_not_found:
    "This workspace link is invalid or you no longer have access. Open the wizard from the dashboard so the URL picks up the right workspace.",
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
    "OAuth credentials for that tracker aren't configured on this deployment. You can still use GitHub Issues — it's already connected via the GitHub App.",
  not_configured_linear:
    "Linear OAuth isn't configured on this deployment (missing LINEAR_CLIENT_ID / LINEAR_CLIENT_SECRET). You can skip for now and use GitHub Issues — it's already connected via the GitHub App — or ask ops to wire Linear credentials.",
  not_configured_notion:
    "Notion OAuth isn't configured on this deployment (missing NOTION_CLIENT_ID / NOTION_CLIENT_SECRET). You can skip for now and use GitHub Issues — it's already connected via the GitHub App — or ask ops to wire Notion credentials.",
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
 * labels in ``<Stepper/>`` to go backwards, e.g. to revisit the repo
 * picker without losing their place.
 *
 * Legacy ``configure`` and ``knowledge`` pins are recognised here too
 * so the back-compat redirect (in :func:`OnboardingPage`) fires
 * before the auto-resume logic can second-guess them.
 */
function hasExplicitStep(raw: string | string[] | undefined): boolean {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return (
    v === "github" ||
    v === "repos" ||
    v === "tracker" ||
    v === "confirm" ||
    // Legacy pre-Wave-8c step ids — kept so bookmarks / email links
    // still hit the new wizard. They funnel to ``confirm`` via the
    // server-side redirect in :func:`OnboardingPage`.
    v === "configure" ||
    v === "knowledge" ||
    v === "done"
  );
}

function pickStep(raw: string | string[] | undefined): StepId {
  const v = Array.isArray(raw) ? raw[0] : raw;
  if (
    v === "repos" ||
    v === "tracker" ||
    v === "confirm" ||
    v === "done"
  ) {
    return v;
  }
  // Pre-Wave-8c legacy ids: ``configure`` was the per-repo preset
  // step, ``knowledge`` was the wizard v1 post-tracker landing pad.
  // Both now mean "go to the new Confirm bootstrap step". The actual
  // ``redirect()`` to ``?step=confirm`` happens server-side in
  // :func:`OnboardingPage` so the URL bar reflects the new id.
  if (v === "configure" || v === "knowledge") return "confirm";
  return "github";
}

/** Legacy step ids that should 303-redirect to the Wave-8c equivalent. */
function legacyStepRedirectTarget(
  raw: string | string[] | undefined,
): StepId | null {
  const v = Array.isArray(raw) ? raw[0] : raw;
  if (v === "configure" || v === "knowledge") return "confirm";
  return null;
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
    // Activated at least one repo → user is past the linear prefix
    // and wants to confirm + bootstrap them. The confirm step is its
    // own landing pad (per-repo cards), and tracker/step-3 is a
    // sibling they can click back to from the stepper if they want
    // to re-do OAuth.
    if (activated.length > 0) return "confirm";
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

  // ── Back-compat redirect for pre-Wave-8c step ids ────────────
  // Bookmarks / email links pointing at ``?step=configure`` (the old
  // 14-preset configure step) and ``?step=knowledge`` (wizard v1)
  // should land on the new Confirm step *with the URL bar reflecting
  // the new id* — otherwise the stepper highlight + analytics drift
  // forever. Run this before any other work so we don't pay for
  // workspace lookups on a request we're about to redirect.
  const legacyTarget = legacyStepRedirectTarget(params.step);
  if (legacyTarget) {
    const search = new URLSearchParams();
    for (const [k, vRaw] of Object.entries(params)) {
      if (k === "step") continue;
      const v = Array.isArray(vRaw) ? vRaw[0] : vRaw;
      if (typeof v === "string") search.set(k, v);
    }
    search.set("step", legacyTarget);
    redirect(`/onboarding?${search.toString()}`);
  }

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

  // Confirm step: load every activated repo plus the per-repo
  // tracker binding and agent-secret status, so the RepoCard client
  // components render fully populated on first paint. We parallelise
  // the per-repo reads with ``Promise.all`` — they're independent.
  let confirmCards: RepoCardInitial[] | null = null;
  let confirmLoadError: string | null = null;
  if (step === "confirm" && wsId && apiConfigured) {
    try {
      const activated = await listActivatedRepos(wsId, sessionToken ?? undefined);
      if (activated.length === 0) {
        // No repos → bounce to the picker. Soft redirect via URL
        // param so the banner explains what happened.
        redirect(
          `/onboarding?step=repos&ws=${encodeURIComponent(wsId)}&error=empty`,
        );
      }
      confirmCards = await Promise.all(
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
      console.error("[onboarding] confirm step load failed", err);
      confirmLoadError = "load_failed";
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
        {step === "confirm" && wsId && (
          <ConfirmStep
            workspaceId={wsId}
            cards={confirmCards}
            loadError={confirmLoadError}
          />
        )}
        {step === "done" && (
          <DoneStep
            wsId={wsId ?? null}
            repoIdParam={pick(params.repo_id) ?? null}
            repoIdsParam={pick(params.repo_ids) ?? null}
          />
        )}
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
        Step 1 of 4 &middot; Connect your code
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
        the ones you want Ship to work with. Every repo gets the same
        canonical Plays bundle — you&apos;ll review it on the Confirm step
        before opening the bootstrap PR.
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
            Wave-8c collapses the 14-preset menu — every repo lands on
            the canonical ``DEFAULT_BUNDLE``. Backend's
            :func:`normalize_preset` maps any legacy preset id (and
            ``None``) to ``"default"``, so we don't need a hidden
            ``preset`` field here at all anymore. Confirm step is the
            new landing pad after this form posts.
          */}

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
      tag: "Reuses GH App \u00b7 no OAuth",
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
        This workspace-level OAuth is reused by <em>every</em> repo — the
        Confirm step lets you override the tracker kind per repo (Linear,
        GitHub Issues, Jira) and the team/project it writes to. OAuth
        tokens are encrypted with the workspace key; the API only ever
        exposes{" "}
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
          <strong className="text-aqua">Repos wired.</strong> Each one will
          install the canonical Plays bundle on the Confirm step — review
          before opening the bootstrap PR.
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

      {message &&
        (error?.startsWith("not_configured") ? (
          <div className="mt-5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
            {message}
          </div>
        ) : (
          <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
            {message}
          </div>
        ))}

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
//
// The "What just happened" done step (P5-09) lives in ``done-step.tsx``
// and reads the seed result the confirm step stashed under
// ``sessionStorage["ship.wizard_seed_result.<repo_id>"]`` (with a
// :func:`getLatestWizardSeed` fallback for tab-reload + cross-device
// flows). The bootstrap error fallback below is unrelated and stays
// inline because it has no per-step state.

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
      default_branch: repo.default_branch,
    },
    tracker,
    agents,
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
