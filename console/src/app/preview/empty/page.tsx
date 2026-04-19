import { AppShell } from "@/components/app-shell";
import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  EmptyState,
  MockBanner,
} from "@/components/ui";

/**
 * Gallery of every empty state in the operator UI, rendered side by side so
 * we can sanity-check what a fresh workspace looks like the moment after
 * onboarding finishes — before any artifact, document, or lane has run.
 */
export default function EmptyStatesGalleryPage() {
  return (
    <AppShell
      kicker="design preview"
      title="Empty states · fresh workspace"
      actions={<ButtonGhost>← back to dashboard</ButtonGhost>}
    >
      <MockBanner />

      <p className="mb-6 max-w-3xl text-sm leading-relaxed text-white/70">
        What an operator sees the first time they land in a brand-new workspace. Each tile mirrors
        the empty state you&apos;d hit at <code className="font-mono text-aqua/85">/&lt;surface&gt;</code>{" "}
        before any catalog overrides, lane runs, documents, members or integrations exist.
      </p>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Surface label="Dashboard" path="/">
          <EmptyState
            title="No lane runs yet"
            body="The daily and retro lanes will populate this dashboard tomorrow at 07:05 UTC. Until then, set up your first project to wire the queues."
            action={
              <div className="flex items-center justify-center gap-2">
                <ButtonGhost>Read the daily/retro guide</ButtonGhost>
                <ButtonPrimary>+ Add project</ButtonPrimary>
              </div>
            }
          />
        </Surface>

        <Surface label="Catalog" path="/catalog">
          <Card>
            <CardHeader
              title="Workspace catalog is empty"
              subtitle="Global catalog is on by default — that&apos;s why your CLI still works."
            />
            <div className="rounded-xl border border-dashed border-white/15 bg-white/[0.02] p-6 text-center">
              <p className="text-sm text-white/75">
                You haven&apos;t added any workspace artifacts yet.
              </p>
              <p className="mt-1.5 text-[11px] text-white/45">
                Register a git repo or local path to overlay the global catalog with your own
                patterns, tools, and workflows.
              </p>
              <div className="mt-4 flex justify-center gap-2">
                <ButtonGhost>Browse global catalog</ButtonGhost>
                <ButtonPrimary>+ Connect artifact repo</ButtonPrimary>
              </div>
              <p className="mt-3 text-[10px] text-white/35">
                <Badge tone="global">global</Badge>{" "}
                <span className="ml-1">98 artifacts available right now from ship/core.</span>
              </p>
            </div>
          </Card>
        </Surface>

        <Surface label="Pull requests" path="/catalog/pull-requests">
          <Card>
            <CardHeader
              title="No pull requests pending"
              subtitle="When a PR opens against a connected artifact repo, it lands here for human approval."
            />
            <div className="grid place-items-center rounded-xl border border-dashed border-white/15 bg-white/[0.02] py-12 text-center">
              <PRGlyph />
              <p className="mt-3 text-sm text-white/75">All clear.</p>
              <p className="mt-1 text-[11px] text-white/45">
                Connect a repo first — once an artifact PR opens, it shows up here within 30s of
                the GitHub webhook.
              </p>
            </div>
          </Card>
        </Surface>

        <Surface label="Knowledge buckets" path="/knowledge">
          <Card>
            <CardHeader
              title="No knowledge buckets"
              subtitle="Drop a runbook, a wiki PDF, or a slide deck — we parse, chunk, and embed it for the CLI."
            />
            <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-aqua/35 bg-aqua/[0.04] py-10 text-center transition hover:border-aqua/55 hover:bg-aqua/[0.07]">
              <UploadGlyph />
              <span className="text-sm font-semibold text-white">Click or drop files to start</span>
              <span className="text-[11px] text-white/55">
                pdf · docx · pptx · md · html — up to 100 MB per file
              </span>
            </label>
            <div className="mt-3 flex items-center justify-between text-[10px] text-white/45">
              <span>Embedding model: text-embedding-3-large</span>
              <span>Storage: workspace pgvector</span>
            </div>
          </Card>
        </Surface>

        <Surface label="Daily &amp; retro" path="/daily">
          <EmptyState
            title="Tomorrow's digest will land here"
            body="The daily lane runs at 07:05 in your workspace timezone. The first retro will fire after seven days of activity."
            action={
              <div className="flex items-center justify-center gap-2">
                <ButtonGhost>Change schedule</ButtonGhost>
                <ButtonPrimary>Run daily now</ButtonPrimary>
              </div>
            }
          />
        </Surface>

        <Surface label="Telemetry" path="/telemetry">
          <EmptyState
            title="Telemetry is opt-in"
            body="Flip it on to see who used which artifact, with what version, from which CI lane. Nothing leaves your workspace boundary."
            action={
              <div className="flex items-center justify-center gap-2">
                <ButtonGhost>Read what we collect</ButtonGhost>
                <ButtonPrimary>Enable telemetry</ButtonPrimary>
              </div>
            }
          />
        </Surface>

        <Surface label="Effectiveness" path="/effectiveness">
          <EmptyState
            title="Need ~14 days of data"
            body="Lead time, throughput, MTTR, and retro follow-through start computing once the daily lane has run for two weeks."
            action={
              <div className="flex items-center justify-center gap-2">
                <ButtonGhost>Read the methodology</ButtonGhost>
                <ButtonPrimary>+ Connect tracker</ButtonPrimary>
              </div>
            }
          />
        </Surface>

        <Surface label="Members" path="/members">
          <Card>
            <CardHeader title="Just you (for now)" subtitle="Invite teammates by email or SSO group" />
            <div className="rounded-xl border border-dashed border-white/15 bg-white/[0.02] p-5">
              <div className="flex items-center justify-between gap-3">
                <input
                  type="email"
                  placeholder="alice@helio.dev, bob@helio.dev…"
                  className="flex-1 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
                />
                <ButtonPrimary>Send invites</ButtonPrimary>
              </div>
              <p className="mt-3 text-[10px] text-white/45">
                Invitees pick their own role on accept. Default role: <Badge tone="info">member</Badge>
              </p>
            </div>
          </Card>
        </Surface>

        <Surface label="Integrations" path="/integrations">
          <Card>
            <CardHeader
              title="No integrations connected"
              subtitle="Wire Linear, GitHub, Slack and OTel one click each."
            />
            <ul className="grid grid-cols-2 gap-2 text-xs">
              {["Linear", "GitHub", "Slack", "OpenTelemetry"].map((name) => (
                <li
                  key={name}
                  className="flex items-center justify-between rounded-lg border border-white/10 bg-white/[0.025] px-3 py-2.5"
                >
                  <span className="font-semibold text-white/85">{name}</span>
                  <button className="rounded-full border border-aqua/40 bg-aqua/10 px-2.5 py-1 text-[10px] font-bold uppercase tracking-widest text-aqua hover:bg-aqua/20">
                    Connect
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        </Surface>

        <Surface label="Workflow runs" path="/workflows">
          <EmptyState
            title="Nothing has run yet"
            body="Workflow lanes appear here the moment they fire. Schedule one or trigger manually from the dashboard."
            action={<ButtonPrimary>Trigger daily lane manually</ButtonPrimary>}
          />
        </Surface>
      </div>
    </AppShell>
  );
}

function Surface({
  label,
  path,
  children,
}: {
  label: string;
  path: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="font-display text-sm font-bold text-white">{label}</h3>
        <code className="font-mono text-[10px] text-white/45">{path}</code>
      </div>
      {children}
    </div>
  );
}

function PRGlyph() {
  return (
    <svg viewBox="0 0 32 32" className="h-10 w-10 text-aqua/85" fill="none" stroke="currentColor" strokeWidth="1.6">
      <circle cx="8" cy="8" r="2.5" />
      <circle cx="8" cy="24" r="2.5" />
      <circle cx="24" cy="24" r="2.5" />
      <path d="M8 10.5V21.5" />
      <path d="M24 21.5V14a4 4 0 0 0-4-4h-4" />
      <path d="M16 7l-3 3 3 3" />
    </svg>
  );
}

function UploadGlyph() {
  return (
    <svg viewBox="0 0 32 32" className="h-9 w-9 text-aqua" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M16 22V8" />
      <path d="M10 14l6-6 6 6" />
      <path d="M6 26h20" />
    </svg>
  );
}
