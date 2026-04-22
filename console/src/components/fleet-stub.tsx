import Link from "next/link";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { Badge, ButtonGhost, Card, CardHeader } from "@/components/ui";

/**
 * PR-1 landing placeholder for the four workspace-unique Fleet
 * surfaces. They all share the same shape — a short "why this
 * exists" explainer, the PR it ships in, and a link back to the
 * workspace home — so the copy stays consistent and the real UIs
 * (PR-2 Fleet Requests, PR-3 Adoption, PR-5 Policy, PR-7 Knowledge
 * graph) slot in without a layout rewrite.
 */

export type FleetStubProps = {
  title: string;
  kicker?: string;
  shipsIn: string;
  summary: ReactNode;
  bullets: string[];
};

export function FleetStub({
  title,
  kicker = "fleet",
  shipsIn,
  summary,
  bullets,
}: FleetStubProps) {
  return (
    <AppShell title={title} kicker={kicker}>
      <section className="max-w-3xl">
        <Card>
          <CardHeader
            title={title}
            subtitle="Workspace-unique primitive — does not aggregate per-repo views."
            action={<Badge tone="info">Ships in {shipsIn}</Badge>}
          />
          <div className="space-y-4 text-sm leading-relaxed text-white/75">
            <p>{summary}</p>
            <ul className="space-y-1.5 pl-4">
              {bullets.map((b) => (
                <li key={b} className="list-disc text-white/70">
                  {b}
                </li>
              ))}
            </ul>
          </div>
          <div className="mt-5 flex flex-wrap gap-2">
            <ButtonGhost>
              <Link href="/">← Workspace home</Link>
            </ButtonGhost>
          </div>
        </Card>
      </section>
    </AppShell>
  );
}
