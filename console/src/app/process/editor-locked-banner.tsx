import Link from "next/link";

import { Card, CardHeader } from "@/components/ui";
import { withWorkspaceQuery } from "@/lib/workspace-scope";

/**
 * Hard-stop banner that locks the /process editor surface when one or
 * more workspace prerequisites are missing.
 *
 * Three checks gate edits:
 *   1. Tracker — at least one workspace-scoped Integration row with
 *      kind in {linear, jira, github} and status === "ok".
 *   2. Orchestrator — at least one NativeIntegrationInstallation
 *      with "orchestrator" in capabilities AND status === "ready".
 *   3. Default agent profile — workspace.default_agent_profile is set
 *      (any value from the validated frozenset).
 *
 * The banner renders ABOVE the editor and the editor itself is rendered
 * read-only (`pointer-events: none` plus disabled save buttons). The
 * operator can still see the existing graph, just not change anything.
 */

export type EditorPrereqStatus = {
  tracker: { ok: boolean; detail: string };
  orchestrator: { ok: boolean; detail: string };
  default_agent: { ok: boolean; detail: string };
};

export function isEditorLocked(status: EditorPrereqStatus): boolean {
  return !(status.tracker.ok && status.orchestrator.ok && status.default_agent.ok);
}

export function EditorLockedBanner({
  workspaceId,
  multiWorkspace,
  status,
}: {
  workspaceId: string;
  multiWorkspace: boolean;
  status: EditorPrereqStatus;
}) {
  const integrationsHref = withWorkspaceQuery(
    "/settings/integrations",
    workspaceId,
    multiWorkspace,
  );
  const generalHref = withWorkspaceQuery(
    "/settings/general",
    workspaceId,
    multiWorkspace,
  );
  const rows = [
    {
      label: "Tracker bound",
      ok: status.tracker.ok,
      detail: status.tracker.detail,
      cta: { href: integrationsHref, label: "Bind tracker" },
    },
    {
      label: "Orchestrator connected",
      ok: status.orchestrator.ok,
      detail: status.orchestrator.detail,
      cta: { href: integrationsHref, label: "Connect runner" },
    },
    {
      label: "Default agent profile set",
      ok: status.default_agent.ok,
      detail: status.default_agent.detail,
      cta: { href: generalHref, label: "Pick default" },
    },
  ];
  return (
    <Card className="border-sun/30 bg-sun/[0.04]">
      <CardHeader
        title="Configure prerequisites before editing this process"
        subtitle="Ship needs a tracker, an orchestrator, and a default agent on the workspace before any process change can run end-to-end. Editor is locked below."
      />
      <ul className="space-y-2">
        {rows.map((row) => (
          <li
            key={row.label}
            className="flex items-center gap-3 rounded-xl border border-white/10 bg-black/15 px-3 py-2"
          >
            <span
              className={[
                "grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-bold",
                row.ok
                  ? "bg-aqua/20 text-aqua"
                  : "bg-coral/20 text-coral",
              ].join(" ")}
              aria-hidden
            >
              {row.ok ? "✓" : "!"}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-white">{row.label}</div>
              <div className="text-[11px] text-white/55">{row.detail}</div>
            </div>
            {!row.ok && (
              <Link
                href={row.cta.href}
                className="shrink-0 rounded-full border border-aqua/30 bg-aqua/10 px-3 py-1 text-[11px] font-bold text-aqua transition hover:bg-aqua/15"
              >
                {row.cta.label} →
              </Link>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}
