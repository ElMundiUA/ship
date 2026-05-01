import type { Metadata } from "next";
import Link from "next/link";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Process",
  description:
    "Every product company runs on processes — not one-shot deliveries, but ongoing work and adjustments. Ship makes those processes legible: each workspace is a graph of processes, each process a sequence of states, routines that fire along the way, and specialists who own each step.",
};

type Specialist = { id: string; name: string; role: string };

const ENGINEERING_SPECIALISTS: Specialist[] = [
  { id: "technical_architect", name: "Technical architect", role: "Plans architecture, migration strategy, boundaries, and technical risks." },
  { id: "developer", name: "Developer", role: "Implements code changes, tests, docs, and prepares PRs." },
  { id: "code_reviewer", name: "Code reviewer", role: "Reviews PRs for correctness, maintainability, risks, and test coverage." },
  { id: "qa_engineer", name: "QA engineer", role: "Validates acceptance criteria, edge cases, and user-facing quality." },
  { id: "qa_automation", name: "QA automation", role: "Adds or maintains automated tests and regression coverage." },
  { id: "devops_platform", name: "DevOps / platform", role: "Handles CI/CD, environments, deployment, infrastructure, and operations." },
  { id: "security_engineer", name: "Security engineer", role: "Reviews auth, permissions, secrets, dependencies, and security policy." },
  { id: "data_ml_engineer", name: "Data / ML engineer", role: "Handles data pipelines, evaluations, experiments, and ML release checks." },
];

const PRODUCT_SPECIALISTS: Specialist[] = [
  { id: "intake", name: "Intake specialist", role: "Clarifies incoming work, checks minimum context, and routes tasks." },
  { id: "business_analyst", name: "Business analyst", role: "Turns ambiguous requests into requirements and acceptance criteria." },
  { id: "product_manager", name: "Product manager", role: "Clarifies scope, priority, tradeoffs, and launch criteria." },
  { id: "designer", name: "Designer", role: "Reviews UX flows, product copy, accessibility intent, and design quality." },
];

const OPERATIONS_SPECIALISTS: Specialist[] = [
  { id: "support_success", name: "Support / success", role: "Turns customer reports into reproducible tasks and validates fixes." },
  { id: "technical_writer", name: "Technical writer", role: "Writes release notes, user docs, internal docs, and runbooks." },
  { id: "marketing_operator", name: "Marketing operator", role: "Handles content, site, campaign, and marketing workflow tasks." },
];

type DevState = { id: string; name: string; specialistId: string; subProcess: "requirements" | "implementation" | "qa" };

const DEV_STATES: DevState[] = [
  { id: "task_intake", name: "Intake", specialistId: "intake", subProcess: "requirements" },
  { id: "ba_requirements", name: "Requirements", specialistId: "business_analyst", subProcess: "requirements" },
  { id: "tech_arch_plan", name: "Architecture plan", specialistId: "technical_architect", subProcess: "implementation" },
  { id: "qa_arch_plan", name: "QA plan", specialistId: "qa_engineer", subProcess: "implementation" },
  { id: "dev_implementation", name: "Implementation", specialistId: "developer", subProcess: "implementation" },
  { id: "qa_manual", name: "Manual QA", specialistId: "qa_engineer", subProcess: "qa" },
  { id: "qa_automation", name: "Automation QA", specialistId: "qa_automation", subProcess: "qa" },
  { id: "pr_review", name: "PR review", specialistId: "code_reviewer", subProcess: "qa" },
];

const SUB_PROCESS_LABEL: Record<DevState["subProcess"], string> = {
  requirements: "Requirements",
  implementation: "Implementation",
  qa: "Quality review",
};

const SPECIALIST_NAME: Record<string, string> = Object.fromEntries(
  [...ENGINEERING_SPECIALISTS, ...PRODUCT_SPECIALISTS, ...OPERATIONS_SPECIALISTS].map((s) => [s.id, s.name]),
);

type Routine = { id: string; name: string; description: string; cron: string };

const DEV_ROUTINES: Routine[] = [
  { id: "daily_security_review", name: "Security review", description: "Scans dependencies and secrets policy on a daily cron.", cron: "06:00 daily" },
  { id: "daily_standup", name: "Daily standup", description: "Asynchronous standup nudge with state and blocker summary.", cron: "09:00 weekdays" },
  { id: "daily_digest", name: "Daily digest", description: "Consolidated summary of in-flight work and blockers.", cron: "08:00 weekdays" },
  { id: "daily_architecture_tests_review", name: "Architecture tests review", description: "Recurring check on test architecture and coverage.", cron: "08:00 weekdays" },
  { id: "daily_technical_architecture_review", name: "Architecture review", description: "Architecture drift and design consistency review.", cron: "10:00 Mondays" },
  { id: "self_heal", name: "Self-heal", description: "Reconciles CI, workflows, and guardrails after failed runs.", cron: "every 2h" },
  { id: "daily_retro", name: "Retro", description: "Lightweight team retro prompts and follow-ups.", cron: "16:00 Fridays" },
  { id: "tech_debt", name: "Tech debt sweep", description: "Triages and sizes technical-debt work for upcoming cycles.", cron: "04:00 Sundays" },
];

type AspirationalState = { name: string; specialistId: string };

type AspirationalProcess = {
  slug: string;
  name: string;
  kicker: string;
  blurb: string;
  states: AspirationalState[];
  routines: { name: string; cadence: string }[];
  accent: "aqua" | "lilac" | "sun" | "coral";
};

const ASPIRATIONAL: AspirationalProcess[] = [
  {
    slug: "marketing",
    name: "Marketing operations",
    kicker: "Marketing",
    blurb: "Briefs, creative, review, ship, measure — using the marketing_operator, designer, technical_writer, devops_platform, and data_ml_engineer specialists already in the catalogue.",
    states: [
      { name: "Brief intake", specialistId: "intake" },
      { name: "Research", specialistId: "marketing_operator" },
      { name: "Creative", specialistId: "designer" },
      { name: "Produce", specialistId: "technical_writer" },
      { name: "Review", specialistId: "marketing_operator" },
      { name: "Ship", specialistId: "devops_platform" },
      { name: "Measure", specialistId: "data_ml_engineer" },
    ],
    routines: [
      { name: "Weekly campaign digest", cadence: "09:00 Mondays" },
      { name: "Monthly attribution review", cadence: "1st of month" },
      { name: "Brand voice audit", cadence: "fortnightly" },
    ],
    accent: "lilac",
  },
  {
    slug: "customer-success",
    name: "Customer success",
    kicker: "Support",
    blurb: "Triage, reproduce, fix, validate, loop back to product — using support_success, developer, qa_engineer, and product_manager.",
    states: [
      { name: "Ticket intake", specialistId: "intake" },
      { name: "Triage", specialistId: "support_success" },
      { name: "Reproduce", specialistId: "support_success" },
      { name: "Fix", specialistId: "developer" },
      { name: "Validate", specialistId: "qa_engineer" },
      { name: "Close", specialistId: "support_success" },
      { name: "Loop back", specialistId: "product_manager" },
    ],
    routines: [
      { name: "Daily unresolved digest", cadence: "08:00 daily" },
      { name: "Weekly pattern review", cadence: "10:00 Fridays" },
      { name: "Monthly CSAT summary", cadence: "1st of month" },
    ],
    accent: "sun",
  },
  {
    slug: "compliance",
    name: "Compliance & security",
    kicker: "Compliance",
    blurb: "Change intake, impact assessment, control check, approval, ship, audit — primarily security_engineer, with developer and devops_platform.",
    states: [
      { name: "Change intake", specialistId: "intake" },
      { name: "Impact assessment", specialistId: "security_engineer" },
      { name: "Control check", specialistId: "security_engineer" },
      { name: "Approval", specialistId: "security_engineer" },
      { name: "Ship", specialistId: "developer" },
      { name: "Audit review", specialistId: "security_engineer" },
    ],
    routines: [
      { name: "Daily dependency scan", cadence: "06:00 daily" },
      { name: "Weekly compliance check", cadence: "07:00 Mondays" },
      { name: "Monthly audit summary", cadence: "1st of month" },
    ],
    accent: "coral",
  },
  {
    slug: "data-ml",
    name: "Data & ML release",
    kicker: "Data / ML",
    blurb: "Experiment intake, data review, eval, train, release decision, monitor — using data_ml_engineer, qa_engineer, and product_manager.",
    states: [
      { name: "Experiment intake", specialistId: "intake" },
      { name: "Data review", specialistId: "data_ml_engineer" },
      { name: "Eval setup", specialistId: "data_ml_engineer" },
      { name: "Train", specialistId: "data_ml_engineer" },
      { name: "Release decision", specialistId: "product_manager" },
      { name: "Monitor", specialistId: "data_ml_engineer" },
    ],
    routines: [
      { name: "Daily drift check", cadence: "06:00 daily" },
      { name: "Weekly eval report", cadence: "09:00 Mondays" },
      { name: "Model card freshness", cadence: "fortnightly" },
    ],
    accent: "aqua",
  },
];

const ACCENT_BORDER: Record<AspirationalProcess["accent"], string> = {
  aqua: "border-aqua/30",
  lilac: "border-lilac/30",
  sun: "border-sun/30",
  coral: "border-coral/30",
};

const ACCENT_KICKER_TEXT: Record<AspirationalProcess["accent"], string> = {
  aqua: "text-aqua/85",
  lilac: "text-lilac/85",
  sun: "text-sun/85",
  coral: "text-coral/85",
};

export default function ProcessPage() {
  return (
    <>
      <SiteHeader />
      <main>
        {/* 1. Hero — universal premise */}
        <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-28 sm:pb-20 sm:pt-32">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-10%,rgba(46,230,214,0.18),transparent_55%)]" />
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_60%_40%_at_85%_30%,rgba(255,200,87,0.10),transparent_60%)]" />
          <div className="relative mx-auto max-w-4xl px-4 text-center sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/90">Process</p>
            <h1 className="font-display mt-4 text-4xl font-bold text-white sm:text-5xl lg:text-[3.25rem] lg:leading-[1.06]">
              Every product company runs on processes.
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-white/70">
              Building a product is not a one-shot delivery. It is a continuous flow of work and adjustments — features
              get scoped, shipped, reviewed, and refined; bugs get triaged, fixed, regressed; releases get planned and
              rolled back. The processes already exist in your team. Ship makes them legible.
            </p>
          </div>
        </section>

        {/* 2. Why processes */}
        <section className="border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto grid max-w-5xl gap-10 px-4 sm:px-6 md:grid-cols-2">
            <div>
              <p className="text-xs font-bold uppercase tracking-[0.2em] text-white/55">The problem</p>
              <h2 className="font-display mt-3 text-2xl font-bold text-white sm:text-3xl">
                Invisible processes drift.
              </h2>
              <p className="mt-4 text-base leading-relaxed text-white/70">
                Most teams know how their work moves — but the knowledge lives in heads, in Slack threads, in the muscle
                memory of three senior engineers. When those heads change roles, the process drifts. When two people
                disagree, neither version wins. The process becomes folklore: real, but unreviewable.
              </p>
            </div>
            <div>
              <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">What changes</p>
              <h2 className="font-display mt-3 text-2xl font-bold text-white sm:text-3xl">
                Written-down processes can be improved.
              </h2>
              <p className="mt-4 text-base leading-relaxed text-white/70">
                A process you can read is a process you can argue about — and improve. Ship gives every workspace a place
                to write its processes down: the states, the order, the conditions, the people responsible. Once they
                are written, the work that follows them is trackable, and the work that breaks them is visible.
              </p>
            </div>
          </div>
        </section>

        {/* 3. Workspace = a graph of processes — show the graph FIRST */}
        <section className="border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-5xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">Step one</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              A workspace is a graph of processes.
            </h2>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/65">
              Every Ship workspace holds a graph. The root node is the workspace itself; below it sit the processes the
              team runs. Today, every workspace ships with one live process — Development — and four drafted ones
              attaching to the same root. As more processes graduate from draft to production depth, the graph fills out.
            </p>
            <WorkspaceGraph />
            <p className="mt-6 text-center text-xs text-white/45">
              Solid edge: live, running today. Dashed edges: drafted shapes attaching to the same workspace root.
            </p>
          </div>
        </section>

        {/* 4. Inside one process — states → routines → specialists */}
        <section className="border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-5xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">Step two</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              Inside a process: states, routines, specialists.
            </h2>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/65">
              Open one process and three named pieces do the work. They build on each other in this order — states first,
              then the routines that fire along the way, then the specialists who play roles inside both.
            </p>

            <div className="mt-12 space-y-10">
              <BuildingBlock
                index="01"
                title="States are the columns work moves through."
                body="A state is a stage of progress. Intake. Analysis. Implementation. Review. Done. Each state has a name, an owner, a capacity (how many items belong there at once), and a set of allowed transitions to other states. The state machine is the contract: work cannot skip review, cannot return to analysis from done, cannot stay in 'in progress' for ninety days without flagging."
              />
              <BuildingBlock
                index="02"
                title="Routines fire on schedule or events."
                body="A routine is a named, recurring job. Daily security review. Architecture sweep. Daily digest. Each routine has a prompt, a default cadence (cron or event trigger), and a defined output — a comment, an Inbox item, a draft article, a tracker update. Routines run inside the process so they always have the right context: which state work is in, which specialist owns it, what evidence has already been gathered."
              />
              <BuildingBlock
                index="03"
                title="Specialists are the roles that run the work."
                body="A specialist is a versioned role definition — intake, business analyst, product manager, developer, technical architect, designer, code reviewer, QA, devops, security, data/ML, support, technical writer, marketing operator. When a routine fires, it takes on a specialist for the duration of the run. Same routine + different specialist = different output. The specialist is the role; the executor (the AI agent — Cursor, Claude Code, Codex, Copilot) is who actually reads the prompt and writes the diff."
              />
            </div>

            <p className="mt-12 max-w-3xl text-sm text-white/50">
              States define <em className="not-italic text-white/70">where</em> work is. Routines define{" "}
              <em className="not-italic text-white/70">when</em> the system acts. Specialists define{" "}
              <em className="not-italic text-white/70">in what role</em>. Get those three right and the rest is
              configuration.
            </p>
          </div>
        </section>

        {/* 5. The Development SDLC fully drawn */}
        <section id="development" className="scroll-mt-24 border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">Step three — the canonical example</p>
              <span className="rounded-full border border-aqua/40 bg-aqua/15 px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.18em] text-aqua">
                Live
              </span>
            </div>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              Development — the SDLC, fully drawn.
            </h2>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/65">
              The Development process is what every workspace gets at activation, and it is the only top-level process
              Ship ships at production depth today. Eight states from intake to PR review, three nested sub-processes
              (Requirements, Implementation, Quality review), eight scheduled routines, owners on every step.
            </p>

            <DevelopmentFlow />

            <div className="mt-12 grid gap-6 items-stretch lg:grid-cols-3">
              <SubProcessCard title="Requirements" states={DEV_STATES.filter((s) => s.subProcess === "requirements")} />
              <SubProcessCard title="Implementation" states={DEV_STATES.filter((s) => s.subProcess === "implementation")} />
              <SubProcessCard title="Quality review" states={DEV_STATES.filter((s) => s.subProcess === "qa")} />
            </div>

            <div className="mt-12">
              <h3 className="font-display text-xl font-bold text-white">Eight routines firing inside the process</h3>
              <p className="mt-2 max-w-3xl text-sm leading-relaxed text-white/60">
                Each routine takes on a specialist for its run, reads the relevant tracker context and knowledge,
                produces evidence (a comment, an Inbox item, a draft). Cron-driven by default; some are event-triggered.
              </p>
              <div className="mt-6 grid gap-3 items-stretch sm:grid-cols-2 lg:grid-cols-4">
                {DEV_ROUTINES.map((routine) => (
                  <div key={routine.id} className="h-full flex flex-col rounded-xl border border-white/10 bg-white/[0.025] p-4">
                    <p className="font-display text-sm font-bold text-white">{routine.name}</p>
                    <p className="mt-2 text-xs leading-relaxed text-white/55">{routine.description}</p>
                    <code className="mt-auto mt-3 inline-block rounded-md bg-black/40 px-2 py-1 font-mono text-[10px] text-aqua/85">
                      {routine.cron}
                    </code>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* 6. Policies — workspace-wide guardrails */}
        <section id="policies" className="scroll-mt-24 border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-coral/85">Above every process</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              Policies are guardrails that apply everywhere.
            </h2>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/65">
              Processes describe <em className="not-italic text-white/85">how</em> work flows. Policies describe what is
              never allowed regardless of which process is running. They sit one layer above and get injected into
              every agent&apos;s system prompt — so a routine can&apos;t accidentally violate a rule the team has
              agreed on, no matter which specialist is on the keyboard.
            </p>

            <div className="mt-12 grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
              <PoliciesShell />
              <div className="flex flex-col gap-4">
                <PolicyExample
                  title="No force-push to main"
                  body="Every executor refuses force-push regardless of which routine asked, which branch they are on, or what the prompt says."
                />
                <PolicyExample
                  title="Cite the source"
                  body="Any summary that quotes knowledge or a tracker item must include a clickable reference. Routines that try to ship without one fail closed."
                />
                <PolicyExample
                  title="Never share customer data"
                  body="Policy guards the data classes the workspace marks as sensitive — they cannot leave designated processes, even in chat."
                />
                <PolicyExample
                  title="Respect on-call"
                  body="No deploy routines fire on the on-call's off hours. The policy reads the on-call schedule integration and gates the execution."
                />
              </div>
            </div>

            <p className="mt-10 max-w-3xl text-sm leading-relaxed text-white/55">
              Policies are versioned the same way specialists are. When the team agrees on a new rule, it lands as a
              diff, gets reviewed, and starts applying on the next routine run — no per-process retrofit needed.
            </p>
          </div>
        </section>

        {/* 7. Knowledge & feedback loop */}
        <section id="knowledge-loop" className="scroll-mt-24 border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-lilac/85">Continuous learning</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              The system gets smarter with every interaction.
            </h2>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/65">
              Every clarification answered, every approval recorded, every retro logged, every casual chat that turns
              into a decision — Ship captures the signal, distills it, and stores it as knowledge. Over time the
              workspace builds an honest model of <em className="not-italic text-white/85">your team&apos;s</em> working
              style: which routines are noisy, which states stall, which specialists need more context, which policies
              need refinement. Then it suggests improvements — for your team, in your voice.
            </p>

            <div className="mt-12 grid items-stretch gap-6 lg:grid-cols-12">
              <div className="lg:col-span-5">
                <FeedbackLoop />
              </div>
              <div className="lg:col-span-7">
                <div className="grid h-full gap-4">
                  <LoopStep
                    n="01"
                    title="Capture"
                    body="Every interaction lands in the audit log: a clarification, an approval, a retro note, a chat thread. Nothing is silently discarded."
                  />
                  <LoopStep
                    n="02"
                    title="Distill"
                    body="The distiller routes raw signal into the right knowledge bucket. Recurring questions become articles. Repeated objections become policy candidates."
                  />
                  <LoopStep
                    n="03"
                    title="Propose"
                    body="The system reads the patterns and proposes improvements: tighten this routine's prompt, split that bucket, move standup an hour later, retire the routine that has produced nothing for three weeks."
                  />
                  <LoopStep
                    n="04"
                    title="Apply"
                    body="A human reviews the proposal and accepts, declines, or defers it — same Inbox, same disposition vocabulary. The accepted change ships and starts producing better signal on the next loop."
                  />
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* 8. Same model, other shapes */}
        <section id="other" className="scroll-mt-24 border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-xs font-bold uppercase tracking-[0.2em] text-white/55">Same model, growing depth</p>
              <span className="rounded-full border border-white/20 bg-white/5 px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.18em] text-white/65">
                Coming soon
              </span>
            </div>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              Four other processes the catalogue can already express.
            </h2>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/65">
              The model — states + routines + specialists — is general. Below are four processes drafted with the same
              building blocks: Marketing operations, Customer success, Compliance &amp; security, Data &amp; ML release.
              The roles already ship in the catalogue; the process shapes are forming. Each one moves from draft to
              production depth as we run it ourselves first.
            </p>

            <div className="mt-10 grid gap-6 items-stretch md:grid-cols-2">
              {ASPIRATIONAL.map((process) => (
                <AspirationalCard key={process.slug} process={process} />
              ))}
            </div>
          </div>
        </section>

        {/* 7. The cast — specialists catalogue (now contextual, not the opening) */}
        <section className="border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">The cast</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              Fifteen specialists ship in the catalogue today.
            </h2>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/65">
              Every process you saw above pulls from the same pool. Engineering, Product, and Operations roles versioned
              like code — bump the role definition once and every routine that runs as that specialist gets the new
              context.
            </p>

            <SpecialistGroup label="Engineering" accent="aqua" specialists={ENGINEERING_SPECIALISTS} />
            <SpecialistGroup label="Product" accent="lilac" specialists={PRODUCT_SPECIALISTS} />
            <SpecialistGroup label="Operations" accent="sun" specialists={OPERATIONS_SPECIALISTS} />
          </div>
        </section>

        {/* 8. Why this beats hardcoded workflows */}
        <section className="border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-5xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-white/55">Why this model</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              Why processes on specialists beats hardcoded workflows.
            </h2>
            <div className="mt-10 grid gap-5 items-stretch md:grid-cols-3">
              {[
                {
                  title: "Specialists are versioned roles",
                  body: "Not 'use the developer agent on this ticket' as a one-line prompt. Specialists are versioned role definitions; Ship records which version ran each step, so a regression in role behaviour traces to a specific role bump, not a vibe shift.",
                },
                {
                  title: "Routines are reviewable jobs",
                  body: "Every scheduled routine has a name, a prompt, a default cadence, and an owner. When a routine drifts, you read the diff. When a routine misbehaves, you point at the row in the audit log.",
                },
                {
                  title: "States carry the contract",
                  body: "The process declares which state allows which transition, what evidence is required, and which specialist owns each step. The tracker mirrors it; routines respect it; humans review it like code.",
                },
              ].map((item) => (
                <div key={item.title} className="h-full flex flex-col rounded-2xl border border-white/10 bg-white/[0.02] p-6">
                  <h3 className="font-display text-lg font-bold text-white">{item.title}</h3>
                  <p className="mt-3 text-sm leading-relaxed text-white/65">{item.body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* 9. CTA */}
        <section className="bg-black/30 py-16 sm:py-20">
          <div className="mx-auto max-w-3xl px-4 text-center sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">Next</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              Map your team&apos;s first process.
            </h2>
            <p className="mt-4 text-base leading-relaxed text-white/70">
              The Development process is what most teams adopt first. Once it is running, the same specialists carry over
              into Marketing, Customer success, Compliance, and Data &amp; ML as those processes mature. The contract is
              the same; the roles are the same; only the shape of the graph differs.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <Link href="/beta" className="btn-primary inline-flex">
                Request closed-beta access
              </Link>
              <Link href="/docs/process/overview" className="btn-secondary inline-flex">
                Read the docs
              </Link>
              <Link href="/roadmap" className="btn-secondary inline-flex">
                See the roadmap
              </Link>
            </div>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}

function BuildingBlock({ index, title, body }: { index: string; title: string; body: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-6 sm:p-8">
      <div className="flex items-start gap-5">
        <span className="font-display shrink-0 text-3xl font-bold text-aqua/70">{index}</span>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-xl font-bold text-white sm:text-2xl">{title}</h3>
          <p className="mt-3 text-base leading-relaxed text-white/70">{body}</p>
        </div>
      </div>
    </div>
  );
}

function SpecialistGroup({
  label,
  accent,
  specialists,
}: {
  label: string;
  accent: "aqua" | "lilac" | "sun";
  specialists: Specialist[];
}) {
  const kicker = accent === "aqua" ? "text-aqua/85" : accent === "lilac" ? "text-lilac/85" : "text-sun/85";
  const dot = accent === "aqua" ? "bg-aqua/80" : accent === "lilac" ? "bg-lilac/80" : "bg-sun/80";
  return (
    <div className="mt-10">
      <p className={`text-[11px] font-bold uppercase tracking-[0.18em] ${kicker}`}>{label}</p>
      <div className="mt-4 grid gap-3 items-stretch sm:grid-cols-2 lg:grid-cols-4">
        {specialists.map((s) => (
          <div key={s.id} className="h-full flex flex-col rounded-xl border border-white/10 bg-white/[0.025] p-4">
            <div className="flex items-center gap-2">
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden />
              <code className="font-mono text-[11px] text-white/55">{s.id}</code>
            </div>
            <p className="font-display mt-2 text-sm font-bold text-white">{s.name}</p>
            <p className="mt-2 text-xs leading-relaxed text-white/60">{s.role}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function DevelopmentFlow() {
  return (
    <div className="mt-10 overflow-x-auto rounded-2xl border border-aqua/25 bg-gradient-to-br from-aqua/[0.04] via-white/[0.02] to-transparent p-6 sm:p-8">
      <div className="flex min-w-[860px] items-stretch gap-2 sm:gap-3">
        {DEV_STATES.map((state, idx) => (
          <div key={state.id} className="flex items-stretch">
            <div className="flex w-[100px] flex-col rounded-xl border border-white/10 bg-black/30 p-3 sm:w-[120px]">
              <p className="text-[9px] font-bold uppercase tracking-[0.16em] text-aqua/70">
                {SUB_PROCESS_LABEL[state.subProcess]}
              </p>
              <p className="font-display mt-2 text-sm font-bold text-white">{state.name}</p>
              <code className="mt-auto pt-3 font-mono text-[9px] text-white/45">{state.id}</code>
              <div className="mt-3 rounded-md bg-aqua/10 px-2 py-1.5 text-[10px] font-semibold text-aqua">
                {SPECIALIST_NAME[state.specialistId]}
              </div>
            </div>
            {idx < DEV_STATES.length - 1 && (
              <div className="flex w-6 items-center justify-center text-white/25" aria-hidden>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function SubProcessCard({ title, states }: { title: string; states: DevState[] }) {
  return (
    <div className="h-full flex flex-col rounded-2xl border border-white/10 bg-white/[0.02] p-5">
      <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-aqua/75">{title}</p>
      <p className="font-display mt-2 text-base font-bold text-white">
        {states.length} {states.length === 1 ? "state" : "states"}
      </p>
      <ul className="mt-4 space-y-2 text-sm text-white/70">
        {states.map((state) => (
          <li key={state.id} className="flex items-baseline justify-between gap-2">
            <span>{state.name}</span>
            <span className="text-xs text-white/45">{SPECIALIST_NAME[state.specialistId]}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AspirationalCard({ process }: { process: AspirationalProcess }) {
  return (
    <div
      className={`relative h-full flex flex-col rounded-2xl border ${ACCENT_BORDER[process.accent]} bg-white/[0.02] p-6`}
      style={{ borderStyle: "dashed" }}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className={`text-[11px] font-bold uppercase tracking-[0.18em] ${ACCENT_KICKER_TEXT[process.accent]}`}>
            {process.kicker}
          </p>
          <h3 className="font-display mt-2 text-xl font-bold text-white">{process.name}</h3>
        </div>
        <span className="shrink-0 rounded-full border border-white/20 bg-white/5 px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.18em] text-white/65">
          Coming soon
        </span>
      </div>
      <p className="mt-3 text-sm leading-relaxed text-white/65">{process.blurb}</p>

      <div className="mt-5">
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">States</p>
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          {process.states.map((state, idx) => (
            <div key={state.name} className="flex items-center gap-1.5">
              <div className="rounded-md border border-white/15 bg-white/[0.04] px-2 py-1.5">
                <p className="text-[11px] font-semibold text-white/85">{state.name}</p>
                <p className="text-[9px] text-white/45">{SPECIALIST_NAME[state.specialistId] ?? state.specialistId}</p>
              </div>
              {idx < process.states.length - 1 && (
                <span className="text-white/25" aria-hidden>
                  ›
                </span>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-5">
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">Drafted routines</p>
        <ul className="mt-2 space-y-1 text-xs text-white/60">
          {process.routines.map((routine) => (
            <li key={routine.name} className="flex items-baseline justify-between gap-2">
              <span>{routine.name}</span>
              <code className="font-mono text-[10px] text-white/40">{routine.cadence}</code>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function WorkspaceGraph() {
  return (
    <div className="mt-10 overflow-x-auto rounded-2xl border border-white/10 bg-black/20 p-6 sm:p-8">
      <svg viewBox="0 0 720 340" className="mx-auto block w-full max-w-3xl" role="img" aria-label="Workspace process graph">
        <g stroke="currentColor" fill="none" strokeLinecap="round">
          <line x1="360" y1="62" x2="360" y2="138" className="text-aqua/60" strokeWidth="2" />
          <line x1="360" y1="62" x2="120" y2="248" className="text-white/25" strokeWidth="1.5" strokeDasharray="4 4" />
          <line x1="360" y1="62" x2="280" y2="248" className="text-white/25" strokeWidth="1.5" strokeDasharray="4 4" />
          <line x1="360" y1="62" x2="440" y2="248" className="text-white/25" strokeWidth="1.5" strokeDasharray="4 4" />
          <line x1="360" y1="62" x2="600" y2="248" className="text-white/25" strokeWidth="1.5" strokeDasharray="4 4" />
        </g>

        <g>
          <rect x="280" y="20" width="160" height="44" rx="22" className="fill-aqua/10 stroke-aqua/60" strokeWidth="1.5" />
          <text x="360" y="48" textAnchor="middle" className="fill-aqua font-display text-[14px] font-bold">
            Workspace
          </text>
        </g>

        <g>
          <rect x="280" y="138" width="160" height="56" rx="14" className="fill-aqua/15 stroke-aqua" strokeWidth="2" />
          <text x="360" y="164" textAnchor="middle" className="fill-white font-display text-[13px] font-bold">
            Development
          </text>
          <text x="360" y="182" textAnchor="middle" className="fill-aqua/80 text-[10px] font-bold uppercase tracking-[0.16em]">
            Live · 8 states
          </text>
        </g>

        {[
          { x: 40, y: 248, label: "Marketing", strokeClass: "stroke-lilac/40" },
          { x: 200, y: 248, label: "Customer success", strokeClass: "stroke-sun/40" },
          { x: 360, y: 248, label: "Compliance", strokeClass: "stroke-coral/40" },
          { x: 520, y: 248, label: "Data & ML", strokeClass: "stroke-aqua/40" },
        ].map((node) => (
          <g key={node.label}>
            <rect
              x={node.x}
              y={node.y}
              width={160}
              height={56}
              rx={14}
              className={`fill-white/[0.03] ${node.strokeClass}`}
              strokeWidth="1.5"
              strokeDasharray="5 4"
            />
            <text x={node.x + 80} y={node.y + 26} textAnchor="middle" className="fill-white/85 font-display text-[12px] font-bold">
              {node.label}
            </text>
            <text x={node.x + 80} y={node.y + 44} textAnchor="middle" className="fill-white/45 text-[9px] font-bold uppercase tracking-[0.16em]">
              Coming soon
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function PoliciesShell() {
  return (
    <div className="relative h-full overflow-hidden rounded-2xl border border-coral/25 bg-gradient-to-br from-coral/[0.06] via-white/[0.02] to-transparent p-6 sm:p-8">
      {/* Decorative shell — concentric rings labelled "POLICIES" */}
      <div className="pointer-events-none absolute -right-8 -top-8 h-40 w-40 rounded-full border border-coral/15" aria-hidden />
      <div className="pointer-events-none absolute -left-8 -bottom-8 h-40 w-40 rounded-full border border-coral/10" aria-hidden />

      <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-coral/85">Workspace policies</p>
      <p className="font-display mt-2 text-lg font-bold text-white sm:text-xl">
        Injected into every agent&apos;s system prompt.
      </p>
      <p className="mt-3 text-sm leading-relaxed text-white/65">
        Below are the processes — Development, Marketing, Support, Compliance, Data &amp; ML. Above them sits the
        policy layer. A policy added today applies to every routine that fires tomorrow, in every process, on
        every executor.
      </p>

      <div className="relative mt-6 rounded-xl border border-white/10 bg-black/30 p-4">
        <p className="text-[9px] font-bold uppercase tracking-[0.18em] text-coral/85">Policy layer · 6 active</p>
        <div className="mt-3 grid grid-cols-3 gap-1.5">
          {["Development", "Marketing", "Customer success", "Compliance", "Data & ML", "Release"].map((p, i) => (
            <div
              key={p}
              className={`rounded-md border border-white/[0.08] px-2 py-2 text-center text-[10px] font-semibold ${i === 0 ? "bg-aqua/10 text-aqua" : "bg-white/[0.02] text-white/55"}`}
            >
              {p}
            </div>
          ))}
        </div>
        <div className="mt-3 flex items-center justify-between text-[9px] font-mono uppercase tracking-[0.16em] text-white/40">
          <span>↑ policies apply to all</span>
          <span className="text-coral/75">workspace-wide</span>
        </div>
      </div>
    </div>
  );
}

function PolicyExample({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.025] p-5">
      <div className="flex items-start gap-3">
        <span className="mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-coral" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="font-display text-sm font-bold text-white sm:text-base">{title}</p>
          <p className="mt-2 text-xs leading-relaxed text-white/60 sm:text-sm">{body}</p>
        </div>
      </div>
    </div>
  );
}

function FeedbackLoop() {
  return (
    <div className="relative h-full overflow-hidden rounded-2xl border border-lilac/25 bg-gradient-to-br from-lilac/[0.06] via-white/[0.02] to-transparent p-6 sm:p-8">
      <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-lilac/85">The loop</p>
      <p className="font-display mt-2 text-lg font-bold text-white sm:text-xl">
        Capture → distill → propose → apply.
      </p>
      <p className="mt-3 text-sm leading-relaxed text-white/65">
        Improvements come from your team&apos;s actual behaviour, not from a generic playbook.
      </p>

      <svg viewBox="0 0 320 280" className="mt-5 h-auto w-full" role="img" aria-label="Feedback loop diagram">
        <defs>
          <radialGradient id="loop-glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="rgba(209,167,255,0.18)" />
            <stop offset="100%" stopColor="rgba(209,167,255,0)" />
          </radialGradient>
          <marker id="arrow-lilac" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L8,5 L0,10 z" fill="rgba(209,167,255,0.85)" />
          </marker>
        </defs>
        <rect x="0" y="0" width="320" height="280" fill="url(#loop-glow)" />

        {/* Four circular nodes around the loop */}
        {[
          { x: 160, y: 38, label: "Capture", sub: "audit log" },
          { x: 280, y: 140, label: "Distill", sub: "knowledge" },
          { x: 160, y: 242, label: "Propose", sub: "Inbox item" },
          { x: 40, y: 140, label: "Apply", sub: "ship the diff" },
        ].map((node) => (
          <g key={node.label}>
            <circle cx={node.x} cy={node.y} r="42" fill="rgba(15,15,25,0.95)" stroke="rgba(209,167,255,0.5)" strokeWidth="1.5" />
            <circle cx={node.x} cy={node.y} r="42" fill="rgba(209,167,255,0.06)" />
            <text x={node.x} y={node.y - 2} textAnchor="middle" className="fill-white font-display text-[12px] font-bold">
              {node.label}
            </text>
            <text x={node.x} y={node.y + 14} textAnchor="middle" className="fill-lilac/70 text-[9px] font-bold uppercase tracking-[0.14em]">
              {node.sub}
            </text>
          </g>
        ))}

        {/* Arc arrows between nodes */}
        <g fill="none" stroke="rgba(209,167,255,0.55)" strokeWidth="1.5" strokeLinecap="round">
          <path d="M 200 56 A 110 110 0 0 1 264 100" markerEnd="url(#arrow-lilac)" />
          <path d="M 264 180 A 110 110 0 0 1 200 224" markerEnd="url(#arrow-lilac)" />
          <path d="M 120 224 A 110 110 0 0 1 56 180" markerEnd="url(#arrow-lilac)" />
          <path d="M 56 100 A 110 110 0 0 1 120 56" markerEnd="url(#arrow-lilac)" />
        </g>

        {/* Center label */}
        <text x="160" y="138" textAnchor="middle" className="fill-white/45 text-[9px] font-bold uppercase tracking-[0.18em]">
          continuous
        </text>
        <text x="160" y="152" textAnchor="middle" className="fill-white/45 text-[9px] font-bold uppercase tracking-[0.18em]">
          improvement
        </text>
      </svg>
    </div>
  );
}

function LoopStep({ n, title, body }: { n: string; title: string; body: string }) {
  return (
    <div className="flex h-full items-start gap-4 rounded-2xl border border-white/10 bg-white/[0.02] p-5 sm:p-6">
      <span className="font-display shrink-0 text-2xl font-bold text-lilac/70 sm:text-3xl">{n}</span>
      <div className="min-w-0 flex-1">
        <p className="font-display text-base font-bold text-white sm:text-lg">{title}</p>
        <p className="mt-2 text-sm leading-relaxed text-white/65">{body}</p>
      </div>
    </div>
  );
}
