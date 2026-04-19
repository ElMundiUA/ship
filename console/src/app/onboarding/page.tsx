/**
 * Onboarding wizard.
 *
 * Repo-driven flow. Each step is a server-rendered card; transitions happen
 * via native form POSTs to `/api/onboard/*` route handlers, which mutate
 * server-side state (workspace, integration, repo files via the backend)
 * then 303-redirect back here with the next `step` set in the query string.
 *
 * Step machine:
 *
 *   1. repo        — POST `/api/onboard/inspect` (also `intent=demo`)
 *   2. workspace   — POST `/api/onboard/workspace`
 *   3. workflows   — POST `/api/onboard/workflows`        (skips if no repo)
 *   4. tracker     — POST `/api/onboard/integration`     (was "integration")
 *   5. knowledge   — POST `/api/onboard/knowledge`        (skips if no repo)
 *   6. token       — client fetch to `/api/onboard/mint-token`
 *   7. done        — single CTA into `/catalog`
 */

import Link from "next/link";

import {
  inspectRepo,
  isApiConfigured,
  listAvailableRepos,
  type ApiAvailableRepo,
  type ApiRepoProfile,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { OnboardingTokenStep } from "./token-step";

export const dynamic = "force-dynamic";

type StepId =
  | "repo"
  | "workspace"
  | "github"
  | "repos"
  | "workflows"
  | "tracker"
  | "knowledge"
  | "token"
  | "done";

const STEPS: { id: StepId; label: string }[] = [
  { id: "repo", label: "Repo" },
  { id: "workspace", label: "Workspace" },
  { id: "github", label: "GitHub" },
  { id: "repos", label: "Pick repos" },
  { id: "workflows", label: "Workflows" },
  { id: "tracker", label: "Tracker" },
  { id: "knowledge", label: "Knowledge" },
  { id: "token", label: "CLI" },
];

const REPO_ERRORS: Record<string, string> = {
  api_unavailable: "Backend not reachable. Set SHIP_API_URL and try again.",
  missing_source: "Paste a repo URL or local path before inspecting.",
  not_found: "We couldn't find that path. Double-check it and try again.",
  not_a_dir: "That path exists but isn't a directory.",
  clone_failed: "Git clone failed (auth, network, or repo missing).",
  clone_timeout: "Git clone took longer than 60s — try a shallow clone or a local path.",
  demo_failed: "Couldn't scaffold the demo repo. Try again or paste a path manually.",
  unknown: "Something went sideways. Please retry.",
};

const WORKSPACE_ERRORS: Record<string, string> = {
  api_unavailable: "Backend not reachable. Set SHIP_API_URL and try again.",
  missing_fields: "Both display name and slug are required.",
  bad_slug: "Slug must be lowercase letters, digits, or dashes (3–64 chars).",
  slug_taken: "That slug is already used in this org. Pick another.",
  unknown: "Something went sideways. Please retry.",
};

const WORKFLOWS_ERRORS: Record<string, string> = {
  api_unavailable: "Backend not reachable.",
  missing_selection: "Tick at least one workflow, or hit Skip.",
  forbidden: "You need admin role on this workspace to install workflows.",
  bad_path: "We lost track of the repo path. Restart from step 1.",
  not_found: "The repo path is no longer reachable. Re-inspect it.",
  unknown: "Something went sideways. Please retry.",
};

const GITHUB_ERRORS: Record<string, string> = {
  api_unavailable: "Backend not reachable.",
  forbidden: "You need admin role on this workspace to install the GitHub App.",
  app_not_configured:
    "GitHub App env vars are missing on the backend (GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY / GITHUB_APP_WEBHOOK_SECRET). Ask ops to wire them up and try again.",
  bad_state:
    "Install link expired or was tampered with. Start the install again from this step.",
  unknown: "Couldn't start the install flow. Try again or skip for now.",
};

const REPOS_ERRORS: Record<string, string> = {
  api_unavailable: "Backend not reachable.",
  forbidden: "You need admin role on this workspace to activate repos.",
  no_install:
    "GitHub App isn't installed on this workspace yet. Step back to GitHub.",
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

const KNOWLEDGE_ERRORS: Record<string, string> = {
  api_unavailable: "Backend not reachable.",
  missing_selection: "Tick at least one bucket, or hit Skip.",
  unknown_bucket: "Unknown bucket id — refresh and retry.",
  forbidden: "You need admin role on this workspace.",
  unknown: "Something went sideways. Please retry.",
};

const WORKFLOW_CATALOG: { id: string; name: string; blurb: string }[] = [
  {
    id: "pr-and-ci-gate",
    name: "PR gate & preview",
    blurb: "Required checks + preview deploy + marker contract for AI agents.",
  },
  {
    id: "scheduled-sdlc-lane",
    name: "Scheduled SDLC lane",
    blurb: "Cron-driven intake → BA → developer with queue discipline.",
  },
  {
    id: "hosted-e2e-regression",
    name: "Hosted E2E regression",
    blurb: "Playwright-style browser regressions on schedule + on demand.",
  },
  {
    id: "parallel-audit-lanes",
    name: "Parallel audit lanes",
    blurb: "Tech / QA / security audits on separate boards from delivery.",
  },
  {
    id: "pipeline-self-heal",
    name: "Pipeline self-heal",
    blurb: "Diagnostics cadence and optional agent repair for flaky CI.",
  },
];

const KNOWLEDGE_BUCKETS: { id: string; name: string; blurb: string }[] = [
  {
    id: "brandbook",
    name: "Brandbook",
    blurb: "Name, tagline, voice, key links — distilled from the README.",
  },
  {
    id: "code-style",
    name: "Code style",
    blurb: "Formatters, linters, house rules — distilled from the configs.",
  },
  {
    id: "testing",
    name: "Testing approach",
    blurb: "Test pyramid + sample command — distilled from the test files.",
  },
];

type SearchParams = Promise<{ [key: string]: string | string[] | undefined }>;

function pick(raw: string | string[] | undefined): string | undefined {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return v ?? undefined;
}

function pickStep(raw: string | string[] | undefined): StepId {
  const v = Array.isArray(raw) ? raw[0] : raw;
  if (
    v === "workspace" ||
    v === "github" ||
    v === "repos" ||
    v === "workflows" ||
    v === "tracker" ||
    v === "knowledge" ||
    v === "token" ||
    v === "done"
  ) {
    return v;
  }
  return "repo";
}

export default async function OnboardingPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const params = await searchParams;
  const step = pickStep(params.step);
  const wsId = pick(params.ws);
  const repo = pick(params.repo);
  const error = pick(params.error);
  const apiConfigured = isApiConfigured();
  // Tour mode: the marketing site links here for a no-login dry run.
  const hasSession = (await getSessionToken()) !== null;

  // For repo-aware steps we re-inspect server-side so we can show the
  // suggested name + recommendations alongside the form. Cached on the
  // backend by source URL, so this is cheap on subsequent renders.
  let profile: ApiRepoProfile | null = null;
  if (
    repo &&
    apiConfigured &&
    hasSession &&
    (step === "workspace" ||
      step === "workflows" ||
      step === "knowledge")
  ) {
    try {
      profile = await inspectRepo(repo);
    } catch {
      profile = null;
    }
  }

  // Repo picker step: pull the live installation set once, server-side.
  // We render the result as a server component (faster first paint than
  // a client useEffect, and matches the pattern of the workflows step).
  // ``reposLoadError`` carries a short error code so the UI can show a
  // helpful banner without leaking backend internals.
  let availableRepos: ApiAvailableRepo[] | null = null;
  let reposLoadError: string | null = null;
  if (step === "repos" && wsId && apiConfigured && hasSession) {
    try {
      availableRepos = await listAvailableRepos(wsId);
    } catch (err) {
      const status =
        err && typeof err === "object" && "status" in err
          ? (err as { status: number }).status
          : 0;
      if (status === 409) reposLoadError = "no_install";
      else if (status === 502) reposLoadError = "bad_token";
      else if (status === 401) reposLoadError = "api_unavailable";
      else reposLoadError = "unknown";
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
        {!hasSession ? (
          <Link href="/login" className="text-xs text-white/55 hover:text-white">
            Sign in instead →
          </Link>
        ) : (
          <Link href="/catalog" className="text-xs text-white/55 hover:text-white">
            Skip to catalog →
          </Link>
        )}
      </header>

      <main className="mx-auto w-full max-w-5xl px-6 pb-20">
        {step !== "done" && <Stepper current={step} />}

        {!apiConfigured && (
          <div className="mb-6 rounded-xl border border-coral/40 bg-coral/10 px-4 py-3 text-xs text-white/85">
            <strong className="text-coral">SHIP_API_URL is not set.</strong> The wizard
            renders, but every transition will fail until the backend URL is configured.
          </div>
        )}

        {step === "repo" && (
          <RepoStep
            error={error}
            initialSource={pick(params.source) ?? ""}
          />
        )}
        {step === "workspace" && (
          <WorkspaceStep
            error={error}
            repo={repo ?? ""}
            profile={profile}
            initialName={pick(params.name) ?? profile?.suggested_name ?? ""}
            initialSlug={pick(params.slug) ?? profile?.suggested_slug ?? ""}
          />
        )}
        {step === "github" && wsId && (
          <GitHubStep
            wsId={wsId}
            repo={repo ?? ""}
            error={error}
            githubReason={pick(params.github)}
          />
        )}
        {step === "github" && !wsId && <MissingWorkspaceNotice />}
        {step === "repos" && wsId && (
          <ReposStep
            wsId={wsId}
            repo={repo ?? ""}
            error={error ?? reposLoadError ?? undefined}
            available={availableRepos}
          />
        )}
        {step === "repos" && !wsId && <MissingWorkspaceNotice />}
        {step === "workflows" && wsId && (
          <WorkflowsStep
            wsId={wsId}
            repo={repo ?? ""}
            profile={profile}
            error={error}
          />
        )}
        {step === "workflows" && !wsId && <MissingWorkspaceNotice />}
        {step === "tracker" && wsId && (
          <TrackerStep
            wsId={wsId}
            repo={repo ?? ""}
            error={error}
            linearStatus={pick(params.linear)}
            notionStatus={pick(params.notion)}
          />
        )}
        {step === "tracker" && !wsId && <MissingWorkspaceNotice />}
        {step === "knowledge" && wsId && (
          <KnowledgeStep
            wsId={wsId}
            repo={repo ?? ""}
            profile={profile}
            error={error}
          />
        )}
        {step === "knowledge" && !wsId && <MissingWorkspaceNotice />}
        {step === "token" && wsId && (
          <OnboardingTokenStep wsId={wsId} seeded={pick(params.seeded)} />
        )}
        {step === "token" && !wsId && <MissingWorkspaceNotice />}
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
          <li key={s.id} className="flex flex-1 min-w-[7rem] items-center gap-2">
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
              <span className="hidden h-px flex-1 bg-white/10 sm:block" aria-hidden />
            )}
          </li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------------------
// Step 1 — Repo
// ---------------------------------------------------------------------------

function RepoStep({
  error,
  initialSource,
}: {
  error?: string;
  initialSource: string;
}) {
  const message = error ? REPO_ERRORS[error] ?? error : null;
  return (
    <div className="grid grid-cols-1 gap-8 lg:grid-cols-[1.05fr_0.95fr]">
      <section>
        <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
          Step 1 of 6
        </p>
        <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
          Point Ship at your repo.
        </h1>
        <p className="mt-3 max-w-md text-sm leading-relaxed text-white/70">
          We&apos;ll inspect the working tree, suggest a workspace name, recommend the
          workflows to install, and (later) seed knowledge buckets from your README and
          configs. Nothing is written until you approve each step.
        </p>

        {message && (
          <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
            {message}
          </div>
        )}

        <form action="/api/onboard/inspect" method="POST" className="mt-7 space-y-4">
          <label className="block">
            <span className="mb-1.5 block text-[11px] font-bold uppercase tracking-widest text-white/55">
              Repo URL or local path
            </span>
            {/*
              Browser autofill (Chrome PasswordManager + extensions like
              1Password / Bitwarden) decorates inputs with style/data
              attrs the moment the form lands in the DOM. When that
              mutation lands during React hydration we get a spurious
              #418 "HTML mismatch". `suppressHydrationWarning` is the
              React-blessed escape hatch — see also the rest of this
              file. */}
            <input
              name="source"
              type="text"
              defaultValue={initialSource}
              placeholder="file:///Users/me/code/aurora  ·  https://github.com/me/aurora"
              className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3.5 py-2.5 font-mono text-xs text-white outline-none focus:border-aqua/40"
              suppressHydrationWarning
            />
            <span className="mt-1 block text-[10px] text-white/45">
              <code className="text-aqua">file://</code> reads directly.{" "}
              <code className="text-aqua">https://</code> /{" "}
              <code className="text-aqua">git@</code> get a shallow clone into the
              backend&apos;s workbench (cached per-URL).
            </span>
          </label>

          <div className="flex items-center justify-between gap-3 pt-2">
            <button
              type="submit"
              name="intent"
              value="demo"
              className="text-xs text-white/55 hover:text-white"
              formNoValidate
            >
              Use a demo repo →
            </button>
            <button
              type="submit"
              name="intent"
              value="inspect"
              className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
            >
              Inspect repo →
            </button>
          </div>
        </form>
      </section>

      <aside className="space-y-4">
        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5 backdrop-blur-xl shadow-card">
          <h3 className="font-display text-base font-bold text-white">What we read</h3>
          <ul className="mt-3 space-y-2.5 text-sm">
            {[
              ["README + package metadata", "Brand voice + project name."],
              ["Language + framework configs", "Suggest the right workflows."],
              [".github/workflows + tests", "Decide what's worth automating next."],
              ["Code style configs", "Seed an opinionated style guide."],
            ].map(([t, b]) => (
              <li key={t} className="flex items-start gap-2.5">
                <span className="mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-aqua" />
                <div>
                  <div className="font-semibold text-white">{t}</div>
                  <div className="text-[11px] text-white/55">{b}</div>
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-[11px] text-white/45">
            We never touch the repo until you click <span className="text-aqua">Install</span>{" "}
            or <span className="text-aqua">Seed</span> further down the wizard.
          </p>
        </div>
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — Workspace
// ---------------------------------------------------------------------------

function WorkspaceStep({
  error,
  repo,
  profile,
  initialName,
  initialSlug,
}: {
  error?: string;
  repo: string;
  profile: ApiRepoProfile | null;
  initialName: string;
  initialSlug: string;
}) {
  const message = error ? WORKSPACE_ERRORS[error] ?? error : null;
  return (
    <div className="grid grid-cols-1 gap-8 lg:grid-cols-[1.05fr_0.95fr]">
      <section>
        <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
          Step 2 of 6
        </p>
        <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
          {profile
            ? `Create the workspace for ${profile.suggested_name}.`
            : "Name your workspace."}
        </h1>
        <p className="mt-3 max-w-md text-sm leading-relaxed text-white/70">
          A workspace is the boundary your team operates inside — its own catalog overrides,
          its own daily/retro queues, its own members. The slug shows up in URLs and CLI
          output, so keep it short.
        </p>

        {message && (
          <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
            {message}
          </div>
        )}

        <form action="/api/onboard/workspace" method="POST" className="mt-7 space-y-4">
          {repo && <input type="hidden" name="repo" value={repo} />}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-[11px] font-bold uppercase tracking-widest text-white/55">
                Display name
              </span>
              <input
                name="name"
                type="text"
                defaultValue={initialName || "HelioLabs"}
                required
                className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3.5 py-2.5 text-sm text-white outline-none focus:border-aqua/40"
                suppressHydrationWarning
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-[11px] font-bold uppercase tracking-widest text-white/55">
                Slug
              </span>
              <div className="flex items-center rounded-lg border border-white/10 bg-white/[0.04]">
                <span className="px-3 py-2.5 font-mono text-xs text-white/45">ship.dev/</span>
                <input
                  name="slug"
                  type="text"
                  defaultValue={initialSlug || "heliolabs"}
                  required
                  pattern="[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?"
                  className="flex-1 bg-transparent py-2.5 pr-3 font-mono text-xs text-white outline-none"
                  suppressHydrationWarning
                />
              </div>
            </label>
          </div>

          <div className="flex items-center justify-between gap-3 pt-2">
            <Link
              href="/onboarding?step=repo"
              className="text-xs text-white/55 hover:text-white"
            >
              ← Change repo
            </Link>
            <button
              type="submit"
              className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
            >
              Create workspace →
            </button>
          </div>
        </form>
      </section>

      <aside className="space-y-4">
        {profile ? (
          <RepoSummary profile={profile} />
        ) : (
          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
            <p className="text-xs text-white/55">
              No repo profile yet — we&apos;ll skip the workflow + knowledge steps.
            </p>
            <Link
              href="/onboarding?step=repo"
              className="mt-3 inline-block text-xs text-aqua hover:underline"
            >
              ← Inspect a repo first
            </Link>
          </div>
        )}
      </aside>
    </div>
  );
}

function RepoSummary({ profile }: { profile: ApiRepoProfile }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5 backdrop-blur-xl shadow-card">
      <h3 className="font-display text-base font-bold text-white">What we found</h3>
      <dl className="mt-3 space-y-2.5 text-xs text-white/75">
        <SummaryRow label="Source" value={profile.source} mono />
        {profile.head_branch && (
          <SummaryRow label="Branch" value={profile.head_branch} mono />
        )}
        {profile.primary_language && (
          <SummaryRow label="Language" value={profile.primary_language} />
        )}
        {profile.frameworks.length > 0 && (
          <SummaryRow label="Frameworks" value={profile.frameworks.join(", ")} />
        )}
        {profile.test_frameworks.length > 0 && (
          <SummaryRow label="Tests" value={profile.test_frameworks.join(", ")} />
        )}
        {profile.ci_systems.length > 0 && (
          <SummaryRow label="CI" value={profile.ci_systems.join(", ")} />
        )}
        <SummaryRow label="Files scanned" value={String(profile.file_count)} />
      </dl>
      {profile.readme_excerpt && (
        <p className="mt-4 line-clamp-4 text-[11px] leading-relaxed text-white/55">
          “{profile.readme_excerpt}”
        </p>
      )}
    </div>
  );
}

function SummaryRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-[10px] uppercase tracking-widest text-white/45">{label}</dt>
      <dd
        className={
          "max-w-[60%] truncate text-right text-white " +
          (mono ? "font-mono text-[11px]" : "text-xs")
        }
        title={value}
      >
        {value}
      </dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — GitHub App install (WOW-onboarding flow)
// ---------------------------------------------------------------------------

function GitHubStep({
  wsId,
  repo,
  error,
  githubReason,
}: {
  wsId: string;
  repo: string;
  error?: string;
  githubReason?: string;
}) {
  const message = error ? GITHUB_ERRORS[error] ?? error : null;
  // ``githubReason`` lands here when the backend redirects us back from
  // the GitHub callback. ``installed`` = success, ``request`` = the user
  // hit org-admin approval and we should tell them to wait.
  const success = githubReason === "installed";
  const requested = githubReason === "request";
  return (
    <section>
      <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
        Step 3 of 7 · Connect your code
      </p>
      <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
        Install Ship on GitHub.
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        We use a GitHub App (not a PAT) so the token is scoped to the repos you
        pick, rotates automatically, and stays revocable from your org settings.
        After install you&apos;ll bounce straight back here.
      </p>

      {success && (
        <div className="mt-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
          <strong className="text-aqua">GitHub App installed.</strong> Webhooks
          armed and per-installation tokens are minted on demand. Now pick which
          repos Ship should wire up.
          <div className="mt-3">
            <Link
              href={`/onboarding?step=repos&ws=${encodeURIComponent(wsId)}${
                repo ? `&repo=${encodeURIComponent(repo)}` : ""
              }`}
              className="inline-flex rounded-full bg-aqua/20 px-3 py-1.5 text-[11px] font-bold text-aqua hover:bg-aqua/30"
            >
              Pick repos to wire →
            </Link>
          </div>
        </div>
      )}

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
        {repo && <input type="hidden" name="repo" value={repo} suppressHydrationWarning />}
        <button
          type="submit"
          className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
        >
          {success ? "Reinstall / pick more repos →" : "Install Ship on GitHub →"}
        </button>
        <Link
          href={`/onboarding?step=repos&ws=${encodeURIComponent(wsId)}${
            repo ? `&repo=${encodeURIComponent(repo)}` : ""
          }`}
          className="text-xs text-white/55 hover:text-white"
        >
          Skip for now →
        </Link>
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
// Step 4 — Workflows
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Step 4 — Pick repos (Day-2 picker, fed by /v1/.../repos/available)
// ---------------------------------------------------------------------------

function ReposStep({
  wsId,
  repo,
  error,
  available,
}: {
  wsId: string;
  repo: string;
  error?: string;
  available: ApiAvailableRepo[] | null;
}) {
  const message = error ? REPOS_ERRORS[error] ?? error : null;
  const skipHref = `/onboarding?step=workflows&ws=${encodeURIComponent(wsId)}${
    repo ? `&repo=${encodeURIComponent(repo)}` : ""
  }`;
  const reinstallHref = `/onboarding?step=github&ws=${encodeURIComponent(wsId)}${
    repo ? `&repo=${encodeURIComponent(repo)}` : ""
  }`;
  // ``available`` is null when the page bailed early (no install, API
  // down). The error code already explains *why*, so we just don't
  // render the list — show the banner + a link back to the github step.
  const repos = available ?? [];
  // Default-tick anything already activated; the user can untick to
  // detach. We also default-tick the first repo when nothing is
  // activated yet — saves a click on the most common single-repo case.
  const hasActivation = repos.some((r) => r.activated);
  return (
    <section>
      <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
        Step 4 of 8 · Pick repos
      </p>
      <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
        Which repos should Ship wire up?
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        We pulled this list straight from your GitHub App installation. Tick
        the ones we should attach default pipelines to. You can change this
        later from the dashboard.
      </p>

      {message && (
        <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
          {message}
          {(error === "no_install" || error === "bad_token") && (
            <>
              {" "}
              <Link href={reinstallHref} className="underline hover:text-white">
                Reinstall the app →
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
          {repo && (
            <input type="hidden" name="repo" value={repo} suppressHydrationWarning />
          )}

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
            <Link href={skipHref} className="text-xs text-white/55 hover:text-white">
              Skip for now →
            </Link>
            <button
              type="submit"
              className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
            >
              Wire selected repos →
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Step 5 — Workflows
// ---------------------------------------------------------------------------

function WorkflowsStep({
  wsId,
  repo,
  profile,
  error,
}: {
  wsId: string;
  repo: string;
  profile: ApiRepoProfile | null;
  error?: string;
}) {
  const message = error ? WORKFLOWS_ERRORS[error] ?? error : null;
  const recommended = new Set(profile?.recommended_workflows ?? []);
  return (
    <div className="grid grid-cols-1 gap-8 lg:grid-cols-[1.05fr_0.95fr]">
      <section>
        <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
          Step 3 of 6
        </p>
        <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
          Approve the workflows to install.
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
          We&apos;ll write each picked workflow as{" "}
          <code className="text-aqua">.github/workflows/&lt;id&gt;.yml</code> in the repo,
          drop the human-readable contract under{" "}
          <code className="text-aqua">.ship/workflows/</code>, and commit everything in one
          tidy commit you can review.
        </p>

        {message && (
          <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
            {message}
          </div>
        )}

        <form action="/api/onboard/workflows" method="POST" className="mt-7 space-y-4">
          <input type="hidden" name="ws" value={wsId} suppressHydrationWarning />
          <input type="hidden" name="repo" value={repo} suppressHydrationWarning />

          <fieldset className="space-y-3">
            <legend className="mb-1.5 text-[11px] font-bold uppercase tracking-widest text-white/55">
              Catalog
            </legend>
            {WORKFLOW_CATALOG.map((w) => (
              <label
                key={w.id}
                className="flex cursor-pointer items-start gap-3 rounded-xl border border-white/10 bg-white/[0.025] p-3 has-[:checked]:border-aqua/50 has-[:checked]:bg-aqua/[0.06]"
              >
                <input
                  type="checkbox"
                  name="workflow"
                  value={w.id}
                  defaultChecked={recommended.has(w.id)}
                  className="mt-1"
                  suppressHydrationWarning
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-white">{w.name}</span>
                    {recommended.has(w.id) && (
                      <span className="rounded bg-aqua/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-aqua">
                        recommended
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-white/55">{w.blurb}</div>
                  <div className="mt-1 font-mono text-[10px] text-white/35">
                    .github/workflows/{w.id}.yml + .ship/workflows/{w.id}.md
                  </div>
                </div>
              </label>
            ))}
          </fieldset>

          <div className="flex items-center justify-between gap-3 pt-2">
            <button
              type="submit"
              name="intent"
              value="skip"
              className="text-xs text-white/55 hover:text-white"
              formNoValidate
            >
              Skip for now →
            </button>
            <button
              type="submit"
              name="intent"
              value="install"
              className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
            >
              Install &amp; commit →
            </button>
          </div>
        </form>
      </section>

      <aside className="space-y-4">
        {profile && <RepoSummary profile={profile} />}
        <div className="rounded-2xl border border-aqua/30 bg-aqua/[0.04] p-5 text-xs text-white/70">
          <strong className="block text-aqua">What gets committed</strong>
          <ul className="mt-2 space-y-1 list-disc pl-5">
            <li>One YAML per workflow under <code>.github/workflows/</code></li>
            <li>Artifact contract under <code>.ship/workflows/&lt;id&gt;.md</code></li>
            <li>Lockfile entry in <code>.ship/lock.yaml</code></li>
          </ul>
          <p className="mt-3">
            All in a single commit titled{" "}
            <code className="text-aqua">ship: install N workflow(s)</code>.
          </p>
        </div>
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 4 — Tracker (was "integration")
// ---------------------------------------------------------------------------

function TrackerStep({
  wsId,
  repo,
  error,
  linearStatus,
  notionStatus,
}: {
  wsId: string;
  repo: string;
  error?: string;
  linearStatus?: string;
  notionStatus?: string;
}) {
  const message = error ? TRACKER_ERRORS[error] ?? error : null;
  const tiles: { id: "linear" | "notion" | "github"; name: string; blurb: string; tag: string }[] = [
    {
      id: "linear",
      name: "Linear",
      blurb:
        "OAuth into your Linear workspace. We can list issues, transition states, and comment on tickets.",
      tag: "OAuth · 1 click",
    },
    {
      id: "notion",
      name: "Notion",
      blurb:
        "OAuth into Notion. Share at least one ticket-shaped database with the integration after approval.",
      tag: "OAuth · 1 click",
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
        Step 5 of 8 · Optional
      </p>
      <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
        Pick a tracker.
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        The daily lane mirrors approved actions as tickets here. OAuth flows
        encrypt the access token with the workspace key — the API only reports{" "}
        <code className="rounded bg-white/5 px-1 py-[1px] text-aqua">has_secret: true</code>{" "}
        from here on. You can skip and wire one up later from{" "}
        <Link href="/integrations" className="text-aqua underline">
          Integrations
        </Link>
        .
      </p>

      {linearStatus === "connected" && (
        <div className="mt-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
          <strong className="text-aqua">Linear connected.</strong> Token saved
          and ready. Pick another tracker or hit Continue below.
        </div>
      )}
      {notionStatus === "connected" && (
        <div className="mt-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
          <strong className="text-aqua">Notion connected.</strong> Remember to
          share a database with the &quot;Ship&quot; integration so we can read
          your queue.
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
            <input type="hidden" name="ws" value={wsId} suppressHydrationWarning />
            {repo && (
              <input
                type="hidden"
                name="repo"
                value={repo}
                suppressHydrationWarning
              />
            )}
            <input type="hidden" name="kind" value={tile.id} suppressHydrationWarning />
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
              {tile.id === "github" ? "Use GitHub Issues →" : `Connect ${tile.name} →`}
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
        {repo && (
          <input
            type="hidden"
            name="repo"
            value={repo}
            suppressHydrationWarning
          />
        )}
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
            Skip for now →
          </button>
          <button
            type="submit"
            className="rounded-full border border-aqua/40 bg-aqua/[0.08] px-4 py-2 text-xs font-bold text-aqua hover:bg-aqua/[0.16]"
          >
            Continue →
          </button>
        </div>
      </form>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Step 5 — Knowledge
// ---------------------------------------------------------------------------

function KnowledgeStep({
  wsId,
  repo,
  profile,
  error,
}: {
  wsId: string;
  repo: string;
  profile: ApiRepoProfile | null;
  error?: string;
}) {
  const message = error ? KNOWLEDGE_ERRORS[error] ?? error : null;
  return (
    <div className="grid grid-cols-1 gap-8 lg:grid-cols-[1.05fr_0.95fr]">
      <section>
        <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
          Step 5 of 6
        </p>
        <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
          Seed your knowledge buckets.
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
          We&apos;ll generate three opinionated markdown docs from what we read in the repo
          and commit them under <code className="text-aqua">.ship/knowledge/</code>. They
          become the first ingestion source for the upcoming knowledge-bucket indexer; today
          you can review them in your editor.
        </p>

        {message && (
          <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
            {message}
          </div>
        )}

        <form action="/api/onboard/knowledge" method="POST" className="mt-7 space-y-3">
          <input type="hidden" name="ws" value={wsId} suppressHydrationWarning />
          <input type="hidden" name="repo" value={repo} suppressHydrationWarning />

          {KNOWLEDGE_BUCKETS.map((b) => (
            <label
              key={b.id}
              className="flex cursor-pointer items-start gap-3 rounded-xl border border-white/10 bg-white/[0.025] p-3 has-[:checked]:border-aqua/50 has-[:checked]:bg-aqua/[0.06]"
            >
              <input
                type="checkbox"
                name="bucket"
                value={b.id}
                defaultChecked
                className="mt-1"
                suppressHydrationWarning
              />
              <div className="min-w-0 flex-1">
                <div className="font-semibold text-white">{b.name}</div>
                <div className="text-[11px] text-white/55">{b.blurb}</div>
                <div className="mt-1 font-mono text-[10px] text-white/35">
                  .ship/knowledge/{b.id}.md
                </div>
              </div>
            </label>
          ))}

          <div className="flex items-center justify-between gap-3 pt-2">
            <button
              type="submit"
              name="intent"
              value="skip"
              className="text-xs text-white/55 hover:text-white"
              formNoValidate
            >
              Skip for now →
            </button>
            <button
              type="submit"
              name="intent"
              value="seed"
              className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
            >
              Seed &amp; commit →
            </button>
          </div>
        </form>
      </section>

      <aside className="space-y-4">
        {profile && <RepoSummary profile={profile} />}
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Misc
// ---------------------------------------------------------------------------

function MissingWorkspaceNotice() {
  return (
    <div className="mx-auto max-w-md rounded-2xl border border-coral/40 bg-coral/10 p-6 text-center text-sm text-white/85">
      <p>We lost track of which workspace you were configuring.</p>
      <Link
        href="/onboarding?step=repo"
        className="mt-4 inline-block rounded-full border border-aqua/50 bg-aqua/10 px-4 py-2 text-xs font-bold text-aqua hover:bg-aqua/20"
      >
        Restart wizard
      </Link>
    </div>
  );
}

function DoneStep() {
  return (
    <section className="mx-auto max-w-xl rounded-3xl border border-aqua/30 bg-aqua/[0.04] p-10 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-aqua/20 text-2xl text-aqua">
        ✓
      </div>
      <h1 className="mt-4 font-display text-3xl font-bold">You&apos;re wired in.</h1>
      <p className="mt-2 text-sm text-white/70">
        Workspace created, workflows installed, tracker connected, knowledge seeded, CLI
        token minted. Head into the catalog and start shipping.
      </p>
      <Link
        href="/catalog"
        className="mt-6 inline-block rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
      >
        Open catalog →
      </Link>
    </section>
  );
}
