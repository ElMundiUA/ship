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
  type ApiRepoProfile,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { OnboardingTokenStep } from "./token-step";

export const dynamic = "force-dynamic";

type StepId =
  | "repo"
  | "workspace"
  | "workflows"
  | "tracker"
  | "knowledge"
  | "token"
  | "done";

const STEPS: { id: StepId; label: string }[] = [
  { id: "repo", label: "Repo" },
  { id: "workspace", label: "Workspace" },
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

const TRACKER_ERRORS: Record<string, string> = {
  api_unavailable: "Backend not reachable.",
  missing_secret: "Paste the API key/token before saving.",
  bad_kind: "Pick one of the supported integrations.",
  forbidden: "You need admin role on this workspace to add an integration.",
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

const INTEGRATION_PRESETS: {
  id: string;
  name: string;
  blurb: string;
  configFields?: { name: string; label: string; placeholder?: string }[];
}[] = [
  {
    id: "linear",
    name: "Linear",
    blurb: "Mirror approved retro action items as Linear issues.",
    configFields: [{ name: "team_id", label: "Team ID", placeholder: "ENG" }],
  },
  {
    id: "jira",
    name: "Jira",
    blurb: "Two-way sync of agent-authored tickets with Jira.",
    configFields: [
      { name: "site", label: "Site", placeholder: "acme.atlassian.net" },
      { name: "project_key", label: "Project key", placeholder: "ENG" },
    ],
  },
  {
    id: "github",
    name: "GitHub",
    blurb: "Issues + PR review handles for catalog merges.",
    configFields: [{ name: "org", label: "Org", placeholder: "your-org" }],
  },
  {
    id: "slack",
    name: "Slack",
    blurb: "Post the daily digest into a channel.",
    configFields: [{ name: "channel", label: "Channel", placeholder: "#ship-daily" }],
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
      // Surface as a soft warning rather than blocking the page.
      profile = null;
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
            installed={pick(params.installed)}
            commit={pick(params.commit)}
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
// Step 3 — Workflows
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
  installed,
  commit,
}: {
  wsId: string;
  repo: string;
  error?: string;
  installed?: string;
  commit?: string;
}) {
  const message = error ? TRACKER_ERRORS[error] ?? error : null;
  return (
    <section>
      <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
        Step 4 of 6 · Optional
      </p>
      <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
        Wire up your tracker.
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        Pick where the daily lane should mirror tickets. Plaintext is encrypted with the
        workspace key and never leaves Postgres — the API only reports{" "}
        <code className="rounded bg-white/5 px-1 py-[1px] text-aqua">has_secret: true</code>{" "}
        from here on. You can skip and configure later from{" "}
        <Link href="/integrations" className="text-aqua underline">
          Integrations
        </Link>
        .
      </p>

      {installed && Number(installed) > 0 && (
        <div className="mt-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
          <strong className="text-aqua">Installed {installed} workflow(s).</strong>{" "}
          {commit && (
            <>
              Commit{" "}
              <code className="font-mono text-aqua">{commit}</code> recorded in your repo.
            </>
          )}
        </div>
      )}

      {message && (
        <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
          {message}
        </div>
      )}

      <form
        action="/api/onboard/integration"
        method="POST"
        className="mt-7 space-y-5"
        suppressHydrationWarning
      >
        <input type="hidden" name="ws" value={wsId} suppressHydrationWarning />
        {repo && <input type="hidden" name="repo" value={repo} suppressHydrationWarning />}

        <fieldset className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <legend className="mb-1.5 text-[11px] font-bold uppercase tracking-widest text-white/55">
            Pick a kind
          </legend>
          {INTEGRATION_PRESETS.map((p, i) => (
            <label
              key={p.id}
              className="flex cursor-pointer items-start gap-3 rounded-xl border border-white/10 bg-white/[0.025] p-3 has-[:checked]:border-aqua/50 has-[:checked]:bg-aqua/[0.06]"
            >
              <input
                type="radio"
                name="kind"
                value={p.id}
                defaultChecked={i === 0}
                className="mt-1"
                suppressHydrationWarning
              />
              <div className="min-w-0">
                <div className="font-semibold text-white">{p.name}</div>
                <div className="text-[11px] text-white/55">{p.blurb}</div>
              </div>
            </label>
          ))}
        </fieldset>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {INTEGRATION_PRESETS[0].configFields?.map((f) => (
            <label key={f.name} className="block">
              <span className="mb-1.5 block text-[11px] font-bold uppercase tracking-widest text-white/55">
                {f.label} <span className="font-normal text-white/35">(optional)</span>
              </span>
              <input
                name={`config_${f.name}`}
                type="text"
                placeholder={f.placeholder}
                className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3.5 py-2.5 text-sm text-white outline-none focus:border-aqua/40"
                suppressHydrationWarning
              />
            </label>
          ))}
        </div>

        <label className="block">
          <span className="mb-1.5 block text-[11px] font-bold uppercase tracking-widest text-white/55">
            Secret
          </span>
          <input
            name="secret"
            type="password"
            autoComplete="off"
            placeholder="lin_api_…   /   xoxb-…   /   ghp_…"
            className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3.5 py-2.5 font-mono text-xs text-white outline-none focus:border-aqua/40"
            suppressHydrationWarning
          />
          <span className="mt-1 block text-[10px] text-white/45">
            Encrypted at rest with{" "}
            <code className="text-aqua">ENCRYPTION_KEY</code> (Fernet). Audit log records who
            saved it and when, never the value itself.
          </span>
        </label>

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
            value="save"
            className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
          >
            Save &amp; continue →
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
