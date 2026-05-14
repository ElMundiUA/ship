/**
 * /local-tracker — dev-only control panel for the Memory adapters
 * (E19 step 5).
 *
 * Backend returns 404 when SHIP_USE_MEMORY_ADAPTERS is off, so on
 * production this page renders an empty-state "not enabled" card.
 * In the laptop-offline profile it lists every memory ticket /
 * project / repo / PR / CI run in the workspace with inline action
 * buttons (bump stage, merge PR, rerun CI, add comment).
 */

import { redirect } from "next/navigation";

import { PageBody, PageHeader } from "@/components/app-shell";
import { Badge, Card } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  getLocalTrackerDashboard,
  type ApiLocalDashboard,
} from "@/lib/api/client";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace } from "@/lib/workspace-scope";
import type { ApiWorkspace } from "@/lib/api/types";

import {
  StageBumpControl,
  PrMergeButton,
  CiRerunButton,
} from "./local-tracker-actions";


export const dynamic = "force-dynamic";


// Stages in the order they walk through the FSM — used as the
// dropdown options when the developer wants to bump a ticket.
const STAGES = [
  "task_intake",
  "ba_requirements",
  "tech_arch_plan",
  "execution",
  "review",
  "completed",
];


export default async function LocalTrackerPage({
  searchParams,
}: {
  searchParams: Promise<{ ws?: string }>;
}) {
  const token = await getCachedSessionToken();
  if (!token) redirect("/login");

  let workspaces: ApiWorkspace[] = [];
  try {
    workspaces = await getCachedWorkspaces();
  } catch {
    workspaces = [];
  }
  const sp = await searchParams;
  const workspaceId = await getResolvedWorkspaceId(sp, workspaces);
  const workspace = pickWorkspace(workspaces, workspaceId);
  if (!workspace) {
    return (
      <>
        <PageHeader title="Local tracker" />
        <PageBody>
          <Card>
            <p className="p-4 text-sm text-muted-foreground">
              No workspace bound. Open one from the picker first.
            </p>
          </Card>
        </PageBody>
      </>
    );
  }

  let dashboard: ApiLocalDashboard | null = null;
  let notEnabled = false;
  try {
    dashboard = await getLocalTrackerDashboard(workspace.id, token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 404) {
      notEnabled = true;
    } else if (err instanceof ApiUnavailableError) {
      notEnabled = true;
    } else {
      throw err;
    }
  }

  return (
    <>
      <PageHeader
        title="Local tracker"
        kicker="Dev surface. Only visible when memory adapters are enabled."
      />
      <PageBody>
        {notEnabled ? (
          <Card>
            <p className="p-4 text-sm text-muted-foreground">
              Memory adapters are not enabled on this backend. Set
              <code className="mx-1 rounded bg-muted px-1 py-0.5">
                SHIP_USE_MEMORY_ADAPTERS=true
              </code>
              and restart the dev stack (
              <code className="mx-1 rounded bg-muted px-1 py-0.5">
                make dev-up
              </code>
              ).
            </p>
          </Card>
        ) : (
          dashboard && (
            <div className="space-y-8">
              {dashboard.projects.length > 0 && (
                <section>
                  <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                    Projects
                  </h2>
                  <Card>
                    <ul className="divide-y divide-border">
                      {dashboard.projects.map((p) => (
                        <li key={p.id} className="p-3 text-sm">
                          <div className="flex items-baseline gap-2">
                            <span className="font-medium">{p.name}</span>
                            <Badge tone="neutral">{p.state}</Badge>
                            <span className="text-xs text-muted-foreground">
                              {p.ticket_count} ticket
                              {p.ticket_count === 1 ? "" : "s"}
                            </span>
                          </div>
                          {p.description && (
                            <p className="mt-1 text-xs text-muted-foreground">
                              {p.description}
                            </p>
                          )}
                        </li>
                      ))}
                    </ul>
                  </Card>
                </section>
              )}

              <section>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                  Tickets
                </h2>
                <Card>
                  {dashboard.tickets.length === 0 ? (
                    <p className="p-4 text-sm text-muted-foreground">
                      No tickets — run{" "}
                      <code className="rounded bg-muted px-1 py-0.5">
                        make dev-seed
                      </code>{" "}
                      to plant the demo set.
                    </p>
                  ) : (
                    <ul className="divide-y divide-border">
                      {dashboard.tickets.map((t) => (
                        <li
                          key={t.display_id}
                          className="p-3 text-sm flex items-start gap-3"
                        >
                          <code className="text-xs text-muted-foreground shrink-0 w-16">
                            {t.display_id}
                          </code>
                          <div className="flex-1 min-w-0">
                            <p className="font-medium">{t.title}</p>
                            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                              <Badge tone="neutral">{t.state}</Badge>
                              {t.stage && (
                                <Badge tone="neutral">
                                  stage:{t.stage}
                                </Badge>
                              )}
                              {t.ticket_type && (
                                <span>type:{t.ticket_type}</span>
                              )}
                            </div>
                          </div>
                          <StageBumpControl
                            workspaceId={workspace.id}
                            displayId={t.display_id}
                            currentStage={t.stage}
                            stages={STAGES}
                          />
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
              </section>

              {dashboard.repos.length > 0 && (
                <section>
                  <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                    Repos
                  </h2>
                  <div className="space-y-4">
                    {dashboard.repos.map((repo) => (
                      <Card key={repo.id}>
                        <div className="p-3 border-b border-border">
                          <div className="flex items-baseline gap-2">
                            <span className="font-medium">
                              {repo.owner}/{repo.name}
                            </span>
                            <span className="text-xs text-muted-foreground">
                              {repo.default_branch}
                            </span>
                          </div>
                          {repo.description && (
                            <p className="mt-1 text-xs text-muted-foreground">
                              {repo.description}
                            </p>
                          )}
                        </div>
                        {repo.pull_requests.length > 0 && (
                          <div className="p-3 border-b border-border">
                            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                              Pull requests
                            </h3>
                            <ul className="space-y-2">
                              {repo.pull_requests.map((pr) => (
                                <li
                                  key={pr.number}
                                  className="flex items-start gap-3 text-sm"
                                >
                                  <code className="text-xs text-muted-foreground shrink-0 w-16">
                                    #{pr.number}
                                  </code>
                                  <div className="flex-1 min-w-0">
                                    <p className="font-medium">{pr.title}</p>
                                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                      <Badge tone="neutral">{pr.state}</Badge>
                                      {pr.merged && (
                                        <Badge tone="neutral">merged</Badge>
                                      )}
                                      <span>
                                        {pr.head} → {pr.base}
                                      </span>
                                    </div>
                                  </div>
                                  {pr.state === "open" && (
                                    <PrMergeButton
                                      workspaceId={workspace.id}
                                      owner={repo.owner}
                                      name={repo.name}
                                      number={pr.number}
                                    />
                                  )}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {repo.recent_runs.length > 0 && (
                          <div className="p-3">
                            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                              Recent CI runs
                            </h3>
                            <ul className="space-y-2">
                              {repo.recent_runs.map((run) => (
                                <li
                                  key={run.id}
                                  className="flex items-start gap-3 text-sm"
                                >
                                  <div className="flex-1 min-w-0">
                                    <p className="font-medium">
                                      {run.workflow_name}{" "}
                                      <span className="text-xs text-muted-foreground">
                                        on {run.branch}
                                      </span>
                                    </p>
                                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                      <Badge tone="neutral">{run.status}</Badge>
                                      {run.conclusion && (
                                        <Badge
                                          tone={
                                            run.conclusion === "success"
                                              ? "neutral"
                                              : "neutral"
                                          }
                                        >
                                          {run.conclusion}
                                        </Badge>
                                      )}
                                    </div>
                                  </div>
                                  <CiRerunButton
                                    workspaceId={workspace.id}
                                    owner={repo.owner}
                                    name={repo.name}
                                    runId={run.id}
                                  />
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </Card>
                    ))}
                  </div>
                </section>
              )}
            </div>
          )
        )}
      </PageBody>
    </>
  );
}
