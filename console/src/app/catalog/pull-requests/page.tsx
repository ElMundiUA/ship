import { AppShell } from "@/components/app-shell";
import {
  Badge,
  ButtonDanger,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
} from "@/components/ui";
import { pullRequests, relativeTime, workspaces } from "@/lib/mock/cloud";

const ws = workspaces[0];

export default function CatalogPullRequestsPage() {
  return (
    <AppShell
      kicker={`${ws.name} · catalog`}
      title="Pull requests waiting for the catalog"
      actions={
        <>
          <ButtonGhost>Refresh from GitHub</ButtonGhost>
          <ButtonPrimary>Open Catalog</ButtonPrimary>
        </>
      }
    >
      <MockBanner />

      <p className="mb-5 max-w-3xl text-sm text-white/65">
        Anyone in your workspace (or the public org, for the global mirror) can
        propose a new pattern, tool, workflow or collection. Reviews live here
        so you don&apos;t have to bounce to GitHub — sign-off here merges the PR
        through the same git branch your CI already protects.
      </p>

      <div className="space-y-4">
        {pullRequests.map((pr) => (
          <Card key={pr.id}>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="neutral">{pr.artifactKind}</Badge>
                  <Badge
                    tone={
                      pr.changeKind === "major"
                        ? "err"
                        : pr.changeKind === "minor"
                          ? "info"
                          : "neutral"
                    }
                  >
                    {pr.changeKind} bump
                  </Badge>
                  <Badge
                    tone={
                      pr.status === "ready"
                        ? "ok"
                        : pr.status === "needs-review"
                          ? "info"
                          : pr.status === "blocked"
                            ? "err"
                            : "neutral"
                    }
                    dot
                  >
                    {pr.status}
                  </Badge>
                  <Badge
                    tone={
                      pr.ci === "passing" ? "ok" : pr.ci === "failing" ? "err" : "warn"
                    }
                  >
                    CI {pr.ci}
                  </Badge>
                  <span className="ml-auto text-[11px] text-white/40">
                    #{pr.number} · opened {relativeTime(pr.openedAt)}
                  </span>
                </div>
                <h3 className="mt-2 font-display text-lg font-bold leading-snug text-white">
                  {pr.title}
                </h3>
                <p className="mt-1 max-w-3xl text-sm text-white/70">{pr.description}</p>
                <div className="mt-3 flex flex-wrap items-center gap-3 text-[11px] text-white/55">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="grid h-5 w-5 place-items-center rounded-full bg-white/10 text-[9px] font-bold text-white/85">
                      {pr.authorAvatarInitials}
                    </span>
                    {pr.author}
                  </span>
                  <span>·</span>
                  <span className="font-mono text-emerald-300">+{pr.diffSummary.added}</span>
                  <span className="font-mono text-coral">−{pr.diffSummary.removed}</span>
                  <span className="font-mono text-white/45">{pr.diffSummary.files} files</span>
                  <span>·</span>
                  <span className="font-mono text-white/55">{pr.artifactKind}/{pr.artifactId}</span>
                </div>
              </div>

              <div className="flex shrink-0 flex-col items-end gap-2">
                <div className="flex gap-2">
                  <ButtonPrimary>Approve & merge</ButtonPrimary>
                  <ButtonDanger>Reject</ButtonDanger>
                </div>
                <ButtonGhost>View diff</ButtonGhost>
              </div>
            </div>

            <DiffPreview kind={pr.artifactKind} />
          </Card>
        ))}
      </div>

      <Card className="mt-6">
        <CardHeader title="Why review here, not in GitHub?" />
        <ul className="grid grid-cols-1 gap-3 text-sm text-white/75 md:grid-cols-3">
          <li className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
            <strong className="text-white">Same git, prettier diff.</strong> Frontmatter + markdown rendered as cards instead of raw YAML.
          </li>
          <li className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
            <strong className="text-white">Workspace context.</strong> Shows whether the change overrides your workspace/project layer or shadows global.
          </li>
          <li className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
            <strong className="text-white">Audit trail.</strong> Every approval/reject lands in the same audit log as your action items, with who + when + token used.
          </li>
        </ul>
      </Card>
    </AppShell>
  );
}

function DiffPreview({ kind }: { kind: string }) {
  return (
    <div className="mt-4 overflow-hidden rounded-lg border border-white/10 bg-black/40 font-mono text-[11px] leading-relaxed">
      <div className="flex items-center justify-between border-b border-white/10 bg-white/[0.04] px-3 py-1.5 text-[10px] uppercase tracking-widest text-white/40">
        <span>artifacts/{kind}s/.../ARTIFACT.md</span>
        <span>YAML frontmatter + markdown body</span>
      </div>
      <pre className="m-0 overflow-x-auto px-3 py-2 text-white/80">
{`  channel: stable
  description: >-
-   Quick recap of yesterday's PRs in three sentences.
+   Quick recap of yesterday's PRs, capped at 5 bullets.
+   Adds a "top blocker" line so scrum bots can pin it.
  spec:
    install_target: prompts/daily/digest.md`}
      </pre>
    </div>
  );
}
