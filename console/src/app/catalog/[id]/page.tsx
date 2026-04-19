import Link from "next/link";
import { notFound } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
} from "@/components/ui";
import {
  artifactReadmes,
  artifactVersions,
  artifacts,
  relativeTime,
  workspaces,
} from "@/lib/mock/cloud";

const ws = workspaces[0];

export default async function ArtifactDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  // Pick winner (project > workspace > global) for the header.
  const matches = artifacts.filter((a) => a.id === id);
  if (matches.length === 0) notFound();
  const rank = (s: (typeof matches)[number]["source"]) =>
    s === "project" ? 3 : s === "workspace" ? 2 : 1;
  const winner = matches.slice().sort((a, b) => rank(b.source) - rank(a.source))[0];
  const layers = matches.slice().sort((a, b) => rank(b.source) - rank(a.source));

  const versions = artifactVersions[id] ?? [];
  const readme = artifactReadmes[id];

  return (
    <AppShell
      kicker={`${ws.name} · catalog`}
      title={winner.name}
      actions={
        <>
          <ButtonGhost>Open in repo</ButtonGhost>
          <ButtonPrimary>Use in project</ButtonPrimary>
        </>
      }
    >
      <MockBanner />

      <div className="mb-5 flex flex-wrap items-center gap-2 text-xs text-white/55">
        <Link href="/catalog" className="hover:text-white">
          Catalog
        </Link>
        <span className="text-white/25">/</span>
        <Badge tone="neutral">{winner.kind}</Badge>
        <span className="font-mono text-white/65">{winner.id}</span>
        <span className="text-white/25">·</span>
        <Badge
          tone={winner.source === "workspace" ? "workspace" : winner.source === "project" ? "project" : "global"}
        >
          effective: {winner.source}
        </Badge>
        {winner.overrides && (
          <span className="text-[10px] text-white/45">overrides {winner.overrides}</span>
        )}
        <span className="ml-auto text-[10px] uppercase tracking-widest text-white/40">
          v{winner.version} · {winner.channel}
        </span>
      </div>

      <p className="mb-6 max-w-3xl text-base leading-relaxed text-white/85">{winner.summary}</p>

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="README"
            subtitle="Rendered from artifacts/{kind}s/{id}/ARTIFACT.md"
          />
          {readme ? (
            <div className="space-y-5 text-sm leading-relaxed text-white/80">
              <p>{readme.intro}</p>

              <div>
                <h4 className="mb-2 font-display text-xs font-bold uppercase tracking-widest text-aqua/85">
                  Usage
                </h4>
                <pre className="overflow-x-auto rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-[12px] text-aqua/90">
{readme.usage}
                </pre>
              </div>

              <div>
                <h4 className="mb-2 font-display text-xs font-bold uppercase tracking-widest text-aqua/85">
                  Inputs
                </h4>
                <table className="min-w-full text-xs">
                  <thead className="text-[10px] uppercase tracking-widest text-white/45">
                    <tr>
                      <th className="py-1.5 pr-3 text-left">Name</th>
                      <th className="py-1.5 pr-3 text-left">Required</th>
                      <th className="py-1.5 pr-3 text-left">Default</th>
                      <th className="py-1.5 text-left">Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {readme.inputs.map((i) => (
                      <tr key={i.name} className="border-t border-white/5">
                        <td className="py-2 pr-3">
                          <code className="font-mono text-aqua/90">{i.name}</code>
                        </td>
                        <td className="py-2 pr-3 text-white/65">{i.required ? "yes" : "no"}</td>
                        <td className="py-2 pr-3 font-mono text-white/65">{i.default ?? "—"}</td>
                        <td className="py-2 text-white/75">{i.help}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div>
                <h4 className="mb-2 font-display text-xs font-bold uppercase tracking-widest text-aqua/85">
                  Outputs
                </h4>
                <ul className="space-y-1 text-xs">
                  {readme.outputs.map((o) => (
                    <li key={o} className="font-mono text-white/75">
                      {o}
                    </li>
                  ))}
                </ul>
              </div>

              <div>
                <h4 className="mb-2 font-display text-xs font-bold uppercase tracking-widest text-aqua/85">
                  Why
                </h4>
                <p className="text-white/75">{readme.rationale}</p>
              </div>
            </div>
          ) : (
            <p className="text-sm text-white/55">No README rendered for this mock entry yet.</p>
          )}
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader title="Use in project" subtitle="Drops a project pin into the selected repo" />
            <div className="space-y-3 text-xs">
              <label className="block">
                <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
                  Workspace
                </span>
                <select className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40">
                  <option>{ws.name}</option>
                  <option>Helio · Payments</option>
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
                  Project
                </span>
                <select className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40">
                  <option>helio-platform-api</option>
                  <option>helio-billing</option>
                  <option>helio-on-call-runbooks</option>
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
                  Pin to version
                </span>
                <select className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40">
                  {versions.map((v) => (
                    <option key={v.version}>
                      {v.version} ({v.channel})
                    </option>
                  ))}
                </select>
              </label>
              <ButtonPrimary className="w-full justify-center !py-2 !text-sm">
                Open PR with project pin
              </ButtonPrimary>
              <p className="text-[10px] leading-snug text-white/45">
                Creates a branch in your project repo with{" "}
                <code className="font-mono text-aqua/85">.ship/artifacts/{winner.kind}s/{winner.id}/PIN.yaml</code>
                {" "}and opens a PR for human sign-off.
              </p>
            </div>
          </Card>

          <Card>
            <CardHeader title="Resolution layers" subtitle="Where this id lives across sources" />
            <ol className="space-y-2 text-xs">
              {layers.map((l, i) => (
                <li
                  key={`${l.source}-${l.version}`}
                  className={
                    "flex items-start gap-2 rounded-lg border p-2.5 " +
                    (i === 0
                      ? "border-aqua/35 bg-aqua/[0.05]"
                      : "border-white/10 bg-white/[0.02] opacity-70")
                  }
                >
                  <Badge
                    tone={l.source === "workspace" ? "workspace" : l.source === "project" ? "project" : "global"}
                  >
                    {l.source}
                  </Badge>
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold text-white">{l.name}</div>
                    <div className="text-[10px] text-white/45">
                      v{l.version} · {l.channel} · {relativeTime(l.updatedAt)}
                    </div>
                  </div>
                  {i === 0 && (
                    <span className="rounded-full bg-aqua/20 px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest text-aqua">
                      effective
                    </span>
                  )}
                </li>
              ))}
            </ol>
          </Card>

          <Card>
            <CardHeader title="Quick CLI" />
            <pre className="overflow-x-auto rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-[11px] text-aqua/90">
{`shipctl ${winner.kind} fetch ${winner.id} \\
  --workspace ${ws.slug} \\
  --version ${winner.version}`}
            </pre>
          </Card>
        </div>
      </section>

      <Card className="mt-8">
        <CardHeader
          title="Version history"
          subtitle={
            versions.length
              ? `${versions.length} versions · git source of truth, this is the index view`
              : "No versions recorded for this mock entry"
          }
          action={<ButtonGhost>Compare versions…</ButtonGhost>}
        />
        <ul className="space-y-3">
          {versions.map((v) => (
            <li
              key={v.version}
              className="flex flex-col gap-3 rounded-xl border border-white/10 bg-white/[0.025] p-4 lg:flex-row lg:items-center lg:justify-between"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <code className="rounded-md bg-white/[0.06] px-2 py-0.5 font-mono text-[12px] font-bold text-aqua/95">
                    {v.version}
                  </code>
                  <Badge
                    tone={v.channel === "stable" ? "ok" : v.channel === "beta" ? "warn" : "info"}
                  >
                    {v.channel}
                  </Badge>
                  <span className="text-[10px] uppercase tracking-widest text-white/45">
                    {relativeTime(v.releasedAt)}
                  </span>
                  <span className="text-[10px] text-white/35">· {v.releasedBy}</span>
                </div>
                <p className="mt-1.5 text-sm text-white/75">{v.notes}</p>
                <div className="mt-2 flex items-center gap-3 text-[11px] font-mono text-white/55">
                  <span className="text-emerald-300">+{v.diffStat.added}</span>
                  <span className="text-coral">−{v.diffStat.removed}</span>
                  <span>{v.diffStat.files} files</span>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <ButtonGhost>View</ButtonGhost>
                <ButtonGhost>Pin to project</ButtonGhost>
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </AppShell>
  );
}
