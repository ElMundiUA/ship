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
  listGitHubInstallCandidates,
  listIntegrations,
  listWorkspaces,
  type ApiActivatedRepo,
  type ApiAgentSecretStatus,
  type ApiAvailableRepo,
  type ApiGitHubInstallCandidate,
  type ApiTrackerBinding,
} from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { getSessionToken } from "@/lib/api/session";

import { ConfirmStep } from "./confirm-step";
import { DoneStep } from "./done-step";
import { OnboardingHub } from "./hub";
import { type RepoCardInitial } from "./repo-card";
import { RolesStep, type RolesStepInitial } from "./roles-step";
import { type WorkspaceDefaultsPanelInitial } from "./workspace-defaults-panel";

export const dynamic = "force-dynamic";

type StepId =
  | "hub"
  | "github"
  | "repos"
  | "tracker"
  | "roles"
  | "confirm"
  | "done";

const STEPS: { id: Exclude<StepId, "hub" | "done">; label: string }[] = [
  { id: "github", label: "Code" },
  { id: "repos", label: "Repos" },
  { id: "tracker", label: "Integrations" },
  { id: "roles", label: "Roles" },
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
  installation_not_found:
    "That GitHub App install is no longer available from your other workspaces. Install fresh on GitHub instead.",
  bad_installation: "Pick a valid installation to reuse.",
  unknown: "Couldn't start the install flow. Try again.",
};

const REPOS_ERRORS: Record<string, string> = {
  api_unavailable: "Backend not reachable.",
  forbidden: "You need admin role on this workspace to activate repos.",
  // ``no_install`` previously said "App isn't installed yet". Caused
  // a contradiction in the UI when step 1 historically completed but
  // the install was later suspended / revoked (org admin pulled
  // permissions, repo selection narrowed, etc.) — the page banner
  // would scream "not installed" while the stepper still showed
  // step 1 with a ✓. New copy covers both first-install and
  // post-install-suspended cases honestly.
  no_install:
    "GitHub App isn't installed or has been suspended on this workspace. Reinstall →",
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
  bad_atlassian_input:
    "Atlassian needs a site, account email, and API token. Fill those fields or skip for now.",
  bad_azure_devops_input:
    "Azure DevOps needs an organization slug and PAT. Fill those fields or skip for now.",
  bad_gitlab_input:
    "GitLab needs a host and PAT. Fill those fields or skip for now.",
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
    v === "hub" ||
    v === "github" ||
    v === "repos" ||
    v === "tracker" ||
    v === "roles" ||
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
    v === "hub" ||
    v === "repos" ||
    v === "tracker" ||
    v === "roles" ||
    v === "confirm" ||
    v === "done"
  ) {
    return v;
  }
  return "github";
}

// ELS-179 (W3) — legacyStepRedirectTarget removed 2026-05-19.
// ``?step=configure`` and ``?step=knowledge`` were retired with the
// Wave-8c cutover; bookmarks pointing at them now hit ``?step=github``
// fallback like any other unrecognised step.

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
    if (activated.length > 0) {
      // Once the workspace has at least one repo with a seed PR
      // recorded (``installed_bundle_version != null``) we treat
      // setup as "done enough" and land the operator on the hub.
      // The hub gives them per-section drill-in instead of a forced
      // tunnel through every step. Setup-tunnel resume kicks back in
      // only when no repo has been seeded yet.
      const anySeeded = activated.some(
        (r) => r.installed_bundle_version !== null,
      );
      if (anySeeded) return "hub";
      // Activated repos but none seeded yet → resume at Roles (step 4),
      // not Confirm. Landing on Confirm skipped the Roles step entirely
      // for anyone auto-resumed; Roles → Confirm is one click for users
      // who already configured it.
      return "roles";
    }
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

  // Live install + activated state. Drives the stepper checkmarks
  // (so step 1 / step 2 reflect *current* truth, not tunnel order)
  // AND the github step's copy ("Install" vs "Manage on GitHub").
  // Both calls are cheap and we run them in parallel; failures are
  // best-effort — falling back to "not installed" is the safer
  // assumption than rendering a stale ✓.
  let githubAppInstalled = false;
  let activatedRepoCount = 0;
  if (wsId && apiConfigured) {
    try {
      const installed = await listAvailableRepos(
        wsId,
        sessionToken ?? undefined,
      );
      githubAppInstalled = true;
      // ``listAvailableRepos`` returns the live install's repos, not
      // the workspace's activations. The activation count comes from
      // the activated-list endpoint — same endpoint resume uses, but
      // we don't want to double-fetch when the resume code path
      // already loaded it. Cheap second call accepted.
      const activated = await listActivatedRepos(
        wsId,
        sessionToken ?? undefined,
      );
      activatedRepoCount = activated.length;
      void installed;
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 409) {
        githubAppInstalled = false;
      }
      // Other errors silently leave the flags at false; the UI then
      // surfaces the standard "not installed" path which is the
      // worst-case-but-safe rendering.
    }
  }

  // Sibling-workspace installs the user can reuse without a GitHub
  // round-trip (ELS-157). Only fetched on the github step when the
  // target workspace has no install row yet.
  let githubInstallCandidates: ApiGitHubInstallCandidate[] = [];
  if (
    step === "github" &&
    wsId &&
    apiConfigured &&
    !githubAppInstalled
  ) {
    try {
      const candidates = await listGitHubInstallCandidates(
        wsId,
        sessionToken ?? undefined,
      );
      githubInstallCandidates = candidates.installations;
    } catch {
      // Best-effort — the Install button remains the fallback path.
    }
  }

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

  // Tracker step: load workspace integrations so we can render
  // "connected" badges on each tile. Pre-fix every tile looked
  // identical regardless of whether the operator had already gone
  // through the OAuth flow for that provider, so a returning user
  // had no way to tell which integrations were live.
  let trackerIntegrationKinds: string[] = [];
  if (step === "tracker" && wsId && apiConfigured) {
    try {
      const integrations = await listIntegrations(
        wsId,
        sessionToken ?? undefined,
      );
      // ``connected`` for tracker-step UX = the integration row
      // exists and (for non-github kinds) has a token. ``github``
      // rides on the App installation token; the install check
      // above already handles its prerequisite.
      trackerIntegrationKinds = integrations
        .filter((i) => i.kind === "github" || i.has_secret)
        .map((i) => i.kind as string);
    } catch {
      // Best-effort — render the step without badges rather than
      // failing the whole render.
    }
  }

  // Roles step: load the workspace's current default-agent-profile,
  // the connected tracker / docs integration kinds, and which kind
  // is currently bound as the workspace tracker. ``RolesStep`` runs
  // entirely client-side after this initial paint so we want it
  // populated on first render — otherwise the operator sees an empty
  // dropdown for half a second on a returning visit.
  let rolesInitial: RolesStepInitial | null = null;
  if (step === "roles" && wsId && apiConfigured) {
    try {
      const [workspaces, integrations] = await Promise.all([
        listWorkspaces(sessionToken ?? undefined),
        listIntegrations(wsId, sessionToken ?? undefined),
      ]);
      const ws = workspaces.find((w) => w.id === wsId);
      // Tracker candidates = integration rows that can play the
      // tracker role AND have a token. ``github`` is special: no
      // Integration row, but the App install token covers it.
      const trackerCandidates: ("linear" | "github" | "jira")[] = [];
      if (githubAppInstalled) trackerCandidates.push("github");
      for (const i of integrations) {
        if (!i.has_secret) continue;
        if (i.kind === "linear" || i.kind === "jira") {
          if (!trackerCandidates.includes(i.kind)) trackerCandidates.push(i.kind);
        }
      }
      // Current tracker = most-recently-updated tracker integration
      // with a token. Falls back to ``github`` if the App is installed
      // and nothing else is wired (the implicit default).
      const trackerRows = integrations
        .filter((i) =>
          (i.kind === "linear" || i.kind === "jira") && i.has_secret,
        )
        .sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
      const currentTrackerKind: "linear" | "github" | "jira" | null =
        trackerRows.length > 0
          ? (trackerRows[0].kind as "linear" | "jira")
          : githubAppInstalled
            ? "github"
            : null;
      const docsCandidates: ("notion" | "confluence")[] = [];
      for (const i of integrations) {
        if (!i.has_secret) continue;
        if (i.kind === "notion" || i.kind === "confluence") {
          if (!docsCandidates.includes(i.kind)) docsCandidates.push(i.kind);
        }
      }
      rolesInitial = {
        workspaceId: wsId,
        trackerCandidates,
        currentTrackerKind,
        currentAgentProfile: ws?.default_agent_profile ?? null,
        docsCandidates,
      };
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 401) {
        redirect("/login?next=%2Fonboarding");
      }
      console.error("[onboarding] roles step load failed", err);
      rolesInitial = {
        workspaceId: wsId,
        trackerCandidates: githubAppInstalled ? ["github"] : [],
        currentTrackerKind: null,
        currentAgentProfile: null,
        docsCandidates: [],
      };
    }
  }

  // Confirm step: load every activated repo plus the per-repo
  // tracker binding and agent-secret status, so the RepoCard client
  // components render fully populated on first paint. We parallelise
  // the per-repo reads with ``Promise.all`` — they're independent.
  let confirmCards: RepoCardInitial[] | null = null;
  let confirmLoadError: string | null = null;
  let rolesComplete =
    rolesInitial !== null &&
    rolesInitial.currentTrackerKind !== null &&
    rolesInitial.currentAgentProfile !== null;
  if (step === "confirm" && wsId && apiConfigured) {
    try {
      const [activated, wsListForRoles, integrationsForRoles] = await Promise.all([
        listActivatedRepos(wsId, sessionToken ?? undefined),
        listWorkspaces(sessionToken ?? undefined).catch(() => [] as Awaited<ReturnType<typeof listWorkspaces>>),
        listIntegrations(wsId, sessionToken ?? undefined).catch(() => [] as Awaited<ReturnType<typeof listIntegrations>>),
      ]);
      // Compute roles completion for the stepper checkmark — the roles
      // data isn't loaded on the confirm step normally, so we fetch it
      // here to avoid showing "4" instead of ✓ when arriving from step 4.
      const wsForRoles = wsListForRoles.find((w) => w.id === wsId);
      const hasTracker =
        integrationsForRoles.some(
          (i) => (i.kind === "linear" || i.kind === "jira") && i.has_secret,
        ) || githubAppInstalled;
      const defaultAgentProfile = wsForRoles?.default_agent_profile ?? null;
      rolesComplete = hasTracker && defaultAgentProfile != null;
      if (activated.length === 0) {
        // No repos → bounce to the picker. Soft redirect via URL
        // param so the banner explains what happened.
        redirect(
          `/onboarding?step=repos&ws=${encodeURIComponent(wsId)}&error=empty`,
        );
      }
      confirmCards = await Promise.all(
        activated.map((r) =>
          loadRepoCardInitial(wsId, r, sessionToken ?? undefined, defaultAgentProfile),
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

  // Hub step: gather the workspace-level snapshot the cards render
  // (repo list, default agent profile, tracker integrations). All
  // failures are non-fatal — empty arrays / null defaults still
  // render a usable "what's missing" view.
  let hubRepos: ApiActivatedRepo[] = [];
  let hubDefaults: WorkspaceDefaultsPanelInitial | null = null;
  if (step === "hub" && wsId && apiConfigured) {
    try {
      const [activated, workspaces, integrations] = await Promise.all([
        listActivatedRepos(wsId, sessionToken ?? undefined),
        listWorkspaces(sessionToken ?? undefined),
        listIntegrations(wsId, sessionToken ?? undefined),
      ]);
      hubRepos = activated;
      const ws = workspaces.find((w) => w.id === wsId);
      const trackerKinds = integrations
        .filter((i) =>
          ["linear", "github", "jira"].includes(i.kind as string),
        )
        .filter((i) => i.kind === "github" || i.has_secret)
        .map((i) => i.kind as string);
      hubDefaults = {
        workspaceId: wsId,
        defaultAgentProfile: ws?.default_agent_profile ?? null,
        trackerKinds,
        // Reaching the hub means at least one repo is seeded, which
        // requires the App install — safe assumption.
        githubAppInstalled: true,
      };
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 401) {
        redirect("/login?next=%2Fonboarding");
      }
      console.error("[onboarding] hub load failed", err);
    }
  }

  return (
    // ELS-183 (W7) — marketing-style chrome stripped 2026-05-19. The
    // wizard now sits inside the regular Console layout vocabulary
    // (no radial-gradient background, no 48px grid overlay, no
    // duplicate Ship brand lockup — `(authed)/layout` already renders
    // the sidebar and identity).
    <div className="min-h-screen bg-ink text-white">
      <header className="mx-auto flex w-full max-w-5xl items-center justify-end px-6 py-6">
        {sessionToken && (
          <Link href="/" className="text-xs text-white/55 hover:text-white">
            Skip to dashboard →
          </Link>
        )}
      </header>

      <main className="mx-auto w-full max-w-5xl px-6 pb-20">
        {step !== "done" &&
          step !== "hub" &&
          wsId && (
            <Stepper
              current={step}
              wsId={wsId}
              done={{
                github: githubAppInstalled,
                repos: activatedRepoCount > 0,
                // ``tracker`` (Integrations step) is "done" when the
                // workspace has any connected integration row OR
                // GitHub Issues is implicitly available via the App.
                tracker:
                  trackerIntegrationKinds.length > 0 || githubAppInstalled,
                // ``roles`` is "done" once the workspace has both a
                // tracker kind chosen AND a default agent profile.
                // We only know this when the roles step is being
                // rendered (otherwise we don't pay for the workspace
                // + integrations fetch on every step). On other
                // steps the cell shows the step number, not a ✓.
                roles: rolesComplete,
                confirm: false /* never auto-checks; confirm is the action */,
              }}
            />
          )}

        {!apiConfigured && (
          <div className="mb-6 rounded-xl border border-coral/40 bg-coral/10 px-4 py-3 text-xs text-white/85">
            <strong className="text-coral">SHIP_API_URL is not set.</strong> The
            wizard renders but every transition will fail until the backend URL
            is configured.
          </div>
        )}

        {step === "hub" && wsId && hubDefaults && (
          <OnboardingHub
            workspaceId={wsId}
            repos={hubRepos}
            githubAppInstalled={true}
            workspaceDefaults={hubDefaults}
          />
        )}
        {step === "github" && wsId && (
          <GitHubStep
            wsId={wsId}
            error={error}
            githubReason={pick(params.github)}
            installed={githubAppInstalled}
            linkCandidates={githubInstallCandidates}
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
            atlassianStatus={pick(params.atlassian)}
            azureDevOpsStatus={pick(params.azure_devops)}
            gitlabStatus={pick(params.gitlab)}
            reposJustWired={pick(params.repos) === "wired"}
            connectedKinds={trackerIntegrationKinds}
            githubAppInstalled={githubAppInstalled}
          />
        )}
        {step === "roles" && wsId && rolesInitial && (
          <RolesStep initial={rolesInitial} />
        )}
        {step === "confirm" && wsId && (
          <ConfirmStep
            workspaceId={wsId}
            cards={confirmCards}
            loadError={confirmLoadError}
            // Reaching the confirm step implies the GitHub App is
            // installed — the wizard funnels through ``step=github``
            // first, and ``listActivatedRepos`` (which fed
            // ``confirmCards`` above) 409s without an install.
            githubAppInstalled={true}
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

function Stepper({
  current,
  wsId,
  done,
}: {
  current: Exclude<StepId, "hub" | "done">;
  wsId: string;
  /**
   * Truth-based "step is satisfied" map. Pre-fix the stepper marked
   * a step ✓ purely by comparing tunnel position (cells left of
   * current = done, cells right = next), which lied whenever the
   * underlying state changed mid-flow — e.g. step 1 stayed ✓ after
   * the GitHub App was suspended on the org side, while step 2 then
   * banner-screamed "App isn't installed". Now the ✓ comes from
   * what the backend actually reports.
   *
   * Keys map to ``StepId``s the stepper renders. Missing keys
   * default to ``false`` (not done).
   */
  done: Partial<Record<Exclude<StepId, "hub" | "done">, boolean>>;
}) {
  return (
    <ol className="mb-10 flex flex-wrap items-center gap-x-2 gap-y-3">
      {STEPS.map((s, i) => {
        const isDone = done[s.id] === true;
        const state: "done" | "current" | "next" =
          s.id === current ? "current" : isDone ? "done" : "next";
        const cellHref =
          state === "current"
            ? null
            : `/onboarding?step=${s.id}&ws=${encodeURIComponent(wsId)}`;
        const Body = (
          <>
            <span
              className={
                "grid h-7 w-7 shrink-0 place-items-center rounded-full border text-[11px] font-bold transition " +
                (state === "current"
                  ? "border-aqua/70 bg-aqua/15 text-aqua"
                  : state === "done"
                  ? "border-aqua/40 bg-aqua/30 text-ink group-hover:border-aqua/70"
                  : "border-white/15 bg-white/[0.02] text-white/55 group-hover:border-white/35 group-hover:text-white/85")
              }
            >
              {state === "done" ? "✓" : i + 1}
            </span>
            <span
              className={
                "truncate text-[11px] uppercase tracking-widest transition " +
                (state === "current"
                  ? "text-white"
                  : "text-white/40 group-hover:text-white/85")
              }
            >
              {s.label}
            </span>
          </>
        );
        return (
          <li
            key={s.id}
            className="flex flex-1 min-w-[7rem] items-center gap-2"
          >
            {cellHref ? (
              <Link
                href={cellHref}
                className="group flex min-w-0 items-center gap-2"
                aria-label={`Go to step: ${s.label}`}
              >
                {Body}
              </Link>
            ) : (
              <div
                className="flex min-w-0 items-center gap-2"
                aria-current="step"
              >
                {Body}
              </div>
            )}
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
  installed,
  linkCandidates,
}: {
  wsId: string;
  error?: string;
  githubReason?: string;
  /** True when ``listAvailableRepos`` succeeded for this workspace —
   * i.e. the App is installed and the install row exists in our DB.
   * Drives the entire copy on this step: pre-fix the page hard-coded
   * "Install Ship on GitHub" wording even when the App was already
   * connected, leaving operators clicking Install on an installed
   * workspace and confusing themselves about state. */
  installed: boolean;
  /** Sibling-workspace installs the user can one-click link (ELS-157). */
  linkCandidates: ApiGitHubInstallCandidate[];
}) {
  const message = error ? GITHUB_ERRORS[error] ?? error : null;
  // Backend redirects to `?step=repos&github=installed` after a
  // successful install; the only `github=` value we ever see on this
  // step is `request` (org-admin approval pending) or nothing at all.
  const requested = githubReason === "request";

  return (
    <section>
      <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
        Step 1 of 5 &middot; Connect your code
      </p>
      <h1 className="mt-2 font-display text-2xl font-bold leading-tight">
        {installed ? "Ship is installed on GitHub." : "Install Ship on GitHub."}
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        {installed ? (
          <>
            Ship has scoped access to the repos you picked during install.
            Manage permissions or revoke the App on GitHub any time —
            nothing else on this step needs your attention unless you
            want to add / remove repos.
          </>
        ) : (
          <>
            We use a GitHub App (not a Personal Access Token) so the
            permission is scoped to the repos you pick, the token rotates
            on its own, and you can revoke it any time from your org
            settings. After you click Install, GitHub takes you through
            the repo picker and bounces you straight back here.
          </>
        )}
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

      {installed ? (
        <div className="mt-7 flex flex-wrap items-center gap-3">
          <a
            href="https://github.com/settings/installations"
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-md bg-aqua/15 px-3 py-1.5 text-sm font-semibold text-aqua transition hover:bg-aqua/25"
          >
            Manage on GitHub &rarr;
          </a>
          <form
            action="/api/onboard/github-install"
            method="POST"
            className="inline"
            suppressHydrationWarning
          >
            <input
              type="hidden"
              name="ws"
              value={wsId}
              suppressHydrationWarning
            />
            <button
              type="submit"
              className="text-xs text-white/55 hover:text-white"
            >
              Reinstall fresh →
            </button>
          </form>
          <Link
            href={`/onboarding?step=repos&ws=${encodeURIComponent(wsId)}`}
            className="ml-auto rounded-full border border-aqua/40 bg-aqua/[0.08] px-4 py-2 text-xs font-semibold text-aqua hover:bg-aqua/[0.16]"
          >
            Continue to repos →
          </Link>
        </div>
      ) : (
        <>
          {linkCandidates.length > 0 && (
            <div className="mt-7 space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-white/55">
                Already installed elsewhere
              </p>
              {linkCandidates.map((candidate) => (
                <form
                  key={candidate.installation_id}
                  action="/api/onboard/github-link"
                  method="POST"
                  className="flex flex-wrap items-center gap-3 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3"
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
                    name="installation_id"
                    value={String(candidate.installation_id)}
                    suppressHydrationWarning
                  />
                  <div className="min-w-0 flex-1 text-sm text-white/85">
                    <span className="font-semibold text-white">
                      {candidate.account_login ?? "GitHub account"}
                    </span>
                    <span className="text-white/55">
                      {" "}
                      from workspace{" "}
                      <span className="font-mono text-aqua/90">
                        {candidate.source_workspace_slug}
                      </span>
                    </span>
                  </div>
                  <button
                    type="submit"
                    data-testid={`onboarding-link-github-${candidate.installation_id}`}
                    className="rounded-full border border-aqua/40 bg-aqua/[0.08] px-4 py-2 text-xs font-semibold text-aqua hover:bg-aqua/[0.16]"
                  >
                    Use existing installation →
                  </button>
                </form>
              ))}
            </div>
          )}

          <form
            action="/api/onboard/github-install"
            method="POST"
            className={cn(
              "flex flex-wrap items-center gap-3",
              linkCandidates.length > 0 ? "mt-5" : "mt-7",
            )}
            suppressHydrationWarning
          >
            <input
              type="hidden"
              name="ws"
              value={wsId}
              suppressHydrationWarning
            />
            <button
              type="submit"
              data-testid="onboarding-install-github"
              className={linkCandidates.length > 0 ? "btn-secondary" : "btn-primary"}
            >
              {linkCandidates.length > 0
                ? "Install on a different account →"
                : "Install Ship on GitHub →"}
            </button>
          </form>
        </>
      )}

      {!installed && (
        <ul className="mt-7 grid grid-cols-1 gap-3 text-xs text-white/65 md:grid-cols-3">
          {[
            [
              "Your source stays in your repo",
              "Ship reads PRs and metadata through the GitHub App — it never copies code off your runner.",
            ],
            [
              "Agents run in your CI on your dime",
              "Every agent call dispatches inside GitHub Actions, paid by your API keys. Ship orchestrates; you control the spend.",
            ],
            [
              "Audited by default",
              "Every action — secrets pushed, tickets moved, PRs merged — lands in a per-workspace audit log.",
            ],
          ].map(([t, b]) => (
            <li
              key={t}
              className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3"
            >
              <div className="font-semibold text-white">{t}</div>
              <div className="mt-1 text-[11px] text-white/55">{b}</div>
            </li>
          ))}
        </ul>
      )}
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
        Step 2 of 5 &middot; Pick repos
      </p>
      <h1 className="mt-2 font-display text-2xl font-bold leading-tight">
        Which repos should Ship watch?
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        We pulled this list straight from your GitHub App installation. Tick
        the ones you want Ship to work with. Every repo gets the same setup
        — you&apos;ll review it on the Confirm step before opening the PR.
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
        <div className="mt-5 rounded-xl border border-white/[0.08] bg-white/[0.02] px-4 py-3 text-xs text-white/70">
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

          <fieldset className="space-y-2 rounded-2xl border border-white/[0.08] bg-white/[0.025] p-3 max-h-[420px] overflow-y-auto">
            <legend className="px-2 text-[11px] font-bold uppercase tracking-widest text-white/55">
              {repos.length} visible repo{repos.length === 1 ? "" : "s"}
            </legend>
            {repos.map((r, i) => {
              const claimedBy = r.claimed_by_workspace_slug;
              const claimed = Boolean(claimedBy);
              return (
                <label
                  key={r.external_id}
                  className={cn(
                    "flex items-start gap-3 rounded-xl border p-3",
                    claimed
                      ? "cursor-not-allowed border-white/5 bg-white/[0.015] opacity-55"
                      : "cursor-pointer border-white/5 bg-white/[0.02] has-[:checked]:border-aqua/50 has-[:checked]:bg-aqua/[0.06]",
                  )}
                  title={
                    claimed
                      ? `Already wired in workspace "${claimedBy}". Deactivate it there first if you want this workspace to own the repo.`
                      : undefined
                  }
                >
                  <input
                    type="checkbox"
                    name="repo_id"
                    value={String(r.external_id)}
                    defaultChecked={
                      r.activated || (!hasActivation && i === 0 && !claimed)
                    }
                    disabled={claimed}
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
                      {claimed && (
                        <span className="rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-amber-200">
                          in {claimedBy}
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
              );
            })}
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
              className="btn-primary"
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
  atlassianStatus,
  azureDevOpsStatus,
  gitlabStatus,
  reposJustWired,
  connectedKinds,
  githubAppInstalled,
}: {
  wsId: string;
  error?: string;
  linearStatus?: string;
  notionStatus?: string;
  atlassianStatus?: string;
  azureDevOpsStatus?: string;
  gitlabStatus?: string;
  reposJustWired: boolean;
  /** Workspace-level Integration kinds that have an OAuth/PAT
   * token on file. Drives the "connected" badge on each tile. */
  connectedKinds: string[];
  /** GitHub App installed on this workspace? Counts the
   * ``github`` integration as connected (it doesn't have its own
   * Integration row). */
  githubAppInstalled: boolean;
}) {
  // Build the connected-tile lookup once. Atlassian uses the Jira
  // kind on the backend (the form here connects to the native-
  // integration store via /api/onboard/tracker-install), so we map
  // ``atlassian`` ↔ ``jira`` for badge purposes.
  const isConnected = (id: string): boolean => {
    if (id === "github") return githubAppInstalled;
    if (id === "atlassian") {
      return connectedKinds.includes("jira") || connectedKinds.includes("confluence");
    }
    return connectedKinds.includes(id);
  };
  const message = error ? TRACKER_ERRORS[error] ?? error : null;
  const tiles: {
    id: "linear" | "notion" | "atlassian" | "azure_devops" | "gitlab" | "github";
    name: string;
    blurb: string;
    tag: string;
    fields?: { name: string; label: string; type?: string; placeholder?: string }[];
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
      id: "atlassian",
      name: "Jira + Confluence",
      blurb:
        "Connect one Atlassian Cloud site. Jira powers corporate tickets; Confluence feeds the curated knowledge index.",
      tag: "API token · Jira + docs",
      fields: [
        { name: "site", label: "Site", placeholder: "yourorg.atlassian.net" },
        { name: "email", label: "Email", type: "email", placeholder: "you@company.com" },
        { name: "api_token", label: "API token", type: "password" },
        { name: "jira_project", label: "Default Jira project", placeholder: "ENG" },
      ],
    },
    {
      id: "azure_devops",
      name: "Azure DevOps",
      blurb:
        "Connect an Azure DevOps organization for corporate repos and pipeline orchestration.",
      tag: "PAT · repos + pipelines",
      fields: [
        { name: "organization", label: "Organization", placeholder: "acme-corp" },
        { name: "project", label: "Default project", placeholder: "Platform" },
        { name: "pat", label: "Personal access token", type: "password" },
      ],
    },
    {
      id: "gitlab",
      name: "GitLab",
      blurb:
        "Connect GitLab.com or self-hosted GitLab for repos, merge requests, and CI status.",
      tag: "PAT · GitLab CI",
      fields: [
        { name: "host", label: "Host", placeholder: "gitlab.com" },
        { name: "group", label: "Default group", placeholder: "platform/core" },
        { name: "pat", label: "Personal access token", type: "password" },
      ],
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
        Step 3 of 5 &middot; Integrations
      </p>
      <h1 className="mt-2 font-display text-2xl font-bold leading-tight">
        Connect your tools.
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        Hook up the tools your team already uses — issue trackers, doc
        stores, repo hosts. Each one can play more than one role
        (Linear/Jira track tickets, Confluence/Notion hold docs); you
        pick which integration plays which role on the next step.
        GitHub Issues comes free with the App you installed in step 1.
      </p>

      {reposJustWired && (
        <div className="mt-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
          <strong className="text-aqua">Repos wired.</strong> Each one gets
          the same Ship setup PR — review before merge.
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
      {atlassianStatus === "connected" && (
        <div className="mt-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
          <strong className="text-aqua">Atlassian connected.</strong> Jira and
          Confluence credentials are stored server-side. You can tune buckets
          and project bindings after bootstrap.
        </div>
      )}
      {azureDevOpsStatus === "connected" && (
        <div className="mt-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
          <strong className="text-aqua">Azure DevOps connected.</strong> Repos
          and pipeline credentials are stored server-side.
        </div>
      )}
      {gitlabStatus === "connected" && (
        <div className="mt-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
          <strong className="text-aqua">GitLab connected.</strong> Repo and CI
          credentials are stored server-side.
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

      <div className="mt-7 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {tiles.map((tile) => {
          // Connected = workspace has an Integration row with a token
          // for this kind (or the GitHub App is installed for the
          // ``github`` tile). Single source of truth — pre-fix this
          // was a per-tile URL-param check that only fired on the
          // round-trip *right after* OAuth completed, so a returning
          // operator saw every tile rendered as "fresh / disconnected"
          // even when half of them were already wired.
          const connected = isConnected(tile.id);
          return (
          <form
            key={tile.id}
            action="/api/onboard/tracker-install"
            method="POST"
            data-connected={String(connected)}
            className={`flex h-full flex-col rounded-2xl border p-4 backdrop-blur-xl shadow-card transition ${
              connected
                ? "border-aqua/50 bg-aqua/[0.07]"
                : "border-white/[0.08] bg-white/[0.02] hover:border-aqua/40"
            }`}
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
              <span
                className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-widest ${
                  connected
                    ? "border-aqua/35 bg-aqua/10 text-aqua"
                    : "border-white/[0.08] bg-white/[0.04] text-white/55"
                }`}
              >
                {connected ? "Connected" : tile.tag}
              </span>
            </div>
            <p className="mt-2 flex-1 text-[12px] leading-relaxed text-white/65">
              {tile.blurb}
            </p>
            {tile.fields && (
              <div className="mt-4 space-y-2">
                {tile.fields.map((field) => (
                  <label key={field.name} className="block">
                    <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
                      {field.label}
                    </span>
                    <input
                      name={field.name}
                      type={field.type ?? "text"}
                      placeholder={field.placeholder}
                      className="w-full rounded border border-white/[0.08] bg-white/[0.04] px-2 py-1.5 text-xs text-white outline-none focus:border-aqua/40"
                      suppressHydrationWarning
                    />
                  </label>
                ))}
              </div>
            )}
            <button
              type="submit"
              disabled={connected || tile.id === "github"}
              className="mt-4 inline-flex items-center justify-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2 text-sm font-semibold text-ink shadow-glow transition hover:brightness-110 active:scale-[0.99] disabled:cursor-default disabled:bg-none disabled:bg-white/[0.05] disabled:text-white/45 disabled:shadow-none"
            >
              {connected
                ? tile.id === "github"
                  ? "Built-in (GitHub App)"
                  : "Connected \u2713"
                : `Connect ${tile.name} \u2192`}
            </button>
          </form>
          );
        })}
      </div>
      <p className="mt-3 text-[11px] text-white/45">
        Need to disconnect or rotate a token? Open{" "}
        <Link href="/integrations" className="text-aqua underline">
          Integrations
        </Link>{" "}
        — same data, more knobs (re-auth, probe, manage).
      </p>

      <form
        action="/api/onboard/tracker-install"
        method="POST"
        className="mt-7 flex items-center justify-between gap-3 border-t border-white/[0.08] pt-5"
        suppressHydrationWarning
      >
        <input type="hidden" name="ws" value={wsId} suppressHydrationWarning />
        <input type="hidden" name="kind" value="skip" suppressHydrationWarning />
        <span className="text-[11px] text-white/45">
          Connect what you need — pick which one plays which role on
          the next step.
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
/** Maps a workspace ``default_agent_profile`` to the agent catalog slug
 *  whose secret becomes required on the confirm step. Abstract profiles
 *  (auto/main/cheaper) default to claude-md — the canonical Ship agent.
 *  Profiles that need no vendor key (local_cli, ship_cloud_agent) return
 *  null so no agent is marked required. */
const PROFILE_TO_REQUIRED_SLUG: Record<string, string | null> = {
  cursor_agent: "cursor-cloud",
  codex_cli: "codex",
  main: "claude-md",
  auto: "claude-md",
  cheaper: "claude-md",
  ship_cloud_agent: null,
  local_cli: null,
};

async function loadRepoCardInitial(
  wsId: string,
  repo: ApiActivatedRepo,
  token: string | undefined,
  defaultAgentProfile: string | null = null,
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

  // Mark the agent matching the workspace's chosen profile as required
  // so the "Open seed PR" button stays blocked until that key is set.
  const requiredSlug =
    defaultAgentProfile != null
      ? (PROFILE_TO_REQUIRED_SLUG[defaultAgentProfile] ?? null)
      : null;
  if (requiredSlug !== null) {
    agents = agents.map((a) =>
      a.slug === requiredSlug ? { ...a, required: true } : a,
    );
  }

  return {
    repo: {
      id: repo.id,
      full_name: repo.full_name,
      default_branch: repo.default_branch,
      installed_bundle_version: repo.installed_bundle_version,
      current_bundle_version: repo.current_bundle_version,
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
