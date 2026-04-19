import { AppShell } from "@/components/app-shell";
import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
} from "@/components/ui";
import { recentRuns, relativeTime, workspaces } from "@/lib/mock/cloud";

const ws = workspaces[0];

export default function WorkflowRunsPage() {
  return (
    <AppShell
      kicker={`${ws.name} · workflows`}
      title="Workflow runs"
      actions={
        <>
          <ButtonGhost>Filter…</ButtonGhost>
          <ButtonPrimary>+ Trigger lane</ButtonPrimary>
        </>
      }
    >
      <MockBanner />
      <Card padded={false} className="overflow-hidden">
        <CardHeader
          className="px-5 pt-5"
          title="All lane runs"
          subtitle="Daily, retro, scheduled and self-heal — across every project in this workspace"
        />
        <table className="min-w-full text-sm">
          <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
            <tr>
              <th className="px-4 py-2 text-left font-semibold">Run</th>
              <th className="px-4 py-2 text-left font-semibold">Lane</th>
              <th className="px-4 py-2 text-left font-semibold">Trigger</th>
              <th className="px-4 py-2 text-left font-semibold">Started</th>
              <th className="px-4 py-2 text-left font-semibold">Duration</th>
              <th className="px-4 py-2 text-left font-semibold">Status</th>
              <th className="px-4 py-2 text-left font-semibold">Highlight</th>
            </tr>
          </thead>
          <tbody>
            {recentRuns.map((r) => (
              <tr key={r.id} className="border-t border-white/5 hover:bg-white/[0.02]">
                <td className="px-4 py-2.5 align-top font-mono text-[11px] text-white/60">{r.id}</td>
                <td className="px-4 py-2.5 align-top font-semibold capitalize text-white">{r.kind}</td>
                <td className="px-4 py-2.5 align-top text-xs text-white/65">{r.trigger}</td>
                <td className="px-4 py-2.5 align-top text-xs text-white/55">{relativeTime(r.startedAt)}</td>
                <td className="px-4 py-2.5 align-top text-xs text-white/65">{r.durationSec}s</td>
                <td className="px-4 py-2.5 align-top">
                  <Badge
                    tone={r.status === "ok" ? "ok" : r.status === "warning" ? "warn" : "err"}
                    dot
                  >
                    {r.status}
                  </Badge>
                </td>
                <td className="px-4 py-2.5 align-top text-xs text-white/75">{r.highlight}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </AppShell>
  );
}
