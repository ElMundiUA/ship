import Link from "next/link";

import { Card, CardHeader } from "@/components/ui";
import type { ApiWorkspace } from "@/lib/api/types";

import { homeEntryHref, looksLikeJitPersonalWorkspace } from "@/lib/workspace-scope";

/**
 * Shown on ``/`` when the account has multiple memberships and the URL
 * does not yet pin ``?ws=`` — avoids treating the auto-provisioned personal
 * shell as the default and bouncing everyone into the onboarding wizard.
 */
export function WorkspaceEntryPicker({
  workspaces,
  skipWizard,
}: {
  workspaces: ApiWorkspace[];
  skipWizard: boolean;
}) {
  return (
    <div className="mx-auto mt-8 w-full max-w-lg">
      <Card>
        <CardHeader
          title="Workspaces you can open"
          subtitle="Select one. If it still has no repos connected, you&apos;ll continue in the setup wizard for that workspace only."
        />
        <ul className="mt-4 space-y-2">
          {workspaces.map((w) => {
            const personal = looksLikeJitPersonalWorkspace(w);
            return (
              <li key={w.id}>
                <Link
                  href={homeEntryHref(w.id, skipWizard)}
                  className="flex items-center justify-between gap-3 rounded-lg border border-white/10 bg-white/[0.04] px-4 py-3 text-left transition hover:border-aqua/35 hover:bg-white/[0.07]"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-semibold text-white">
                      {w.name}
                    </span>
                    <span className="block truncate font-mono text-[11px] text-white/45">
                      {w.slug}
                    </span>
                  </span>
                  {personal && (
                    <span className="shrink-0 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-white/50">
                      Personal
                    </span>
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      </Card>
    </div>
  );
}
