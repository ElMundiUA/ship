/**
 * Navigator memory page (E17/ELS-130).
 *
 * Strict personal scope — the backend filters by ``owner_user_id =
 * current_user.id`` so workspace admins cannot read someone else's
 * facts here. The page lists every mem0 fact the operator's chat
 * sessions extracted, grouped by project (or "general-purpose" for
 * untagged facts), with delete + bulk-forget controls.
 *
 * Server component for the read; the row-level delete and the
 * bulk-forget submit live in a small client island below
 * (``memory-actions.tsx``).
 */

import { redirect } from "next/navigation";

import { PageBody, PageHeader } from "@/components/app-shell";
import { Badge, Card } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  listNavigatorMemories,
  type ApiNavigatorMemoriesList,
} from "@/lib/api/client";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace } from "@/lib/workspace-scope";
import type { ApiWorkspace } from "@/lib/api/types";

import { MemoryActions, MemoryRowDelete } from "./memory-actions";


export const dynamic = "force-dynamic";


export default async function MemoryPage({
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
        <PageHeader title="Memory" />
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

  let list: ApiNavigatorMemoriesList | null = null;
  try {
    list = await listNavigatorMemories(workspace.id, {
      limit: 200,
      token,
    });
  } catch (err) {
    if (err instanceof ApiUnavailableError || err instanceof ApiHttpError) {
      list = { items: [], total: 0 };
    } else {
      throw err;
    }
  }

  const items = list?.items ?? [];
  const groups = groupByProject(items);

  return (
    <>
      <PageHeader title="Memory" kicker="Only you can see this page." />
      <PageBody>
        <MemoryActions workspaceId={workspace.id} />
        {items.length === 0 ? (
          <Card>
            <p className="p-4 text-sm text-muted-foreground">
              No memories yet. Ship's Navigator extracts facts from
              your chats automatically — keep talking and they'll
              show up here.
            </p>
          </Card>
        ) : (
          <div className="space-y-6 mt-4">
            {groups.map((g) => (
              <section key={g.key}>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                  {g.label}{" "}
                  <span className="text-xs text-muted-foreground/70">
                    ({g.items.length})
                  </span>
                </h2>
                <Card>
                  <ul className="divide-y divide-border">
                    {g.items.map((m) => (
                      <li key={m.id} className="p-3 text-sm">
                        <div className="flex items-start gap-3">
                          <div className="flex-1 min-w-0">
                            <p className="whitespace-pre-wrap break-words">
                              {m.fact_text}
                            </p>
                            <div className="mt-1 flex flex-wrap gap-2 items-center text-xs text-muted-foreground">
                              {m.intent_at_capture && (
                                <Badge tone="neutral">
                                  captured under {m.intent_at_capture}
                                </Badge>
                              )}
                              <span>
                                {new Date(m.created_at).toLocaleString()}
                              </span>
                              {m.source_thread_id && (
                                <a
                                  href={`/chat?thread=${m.source_thread_id}`}
                                  className="underline hover:text-foreground"
                                >
                                  source chat ↗
                                </a>
                              )}
                            </div>
                          </div>
                          <MemoryRowDelete
                            workspaceId={workspace.id}
                            memoryId={m.id}
                          />
                        </div>
                      </li>
                    ))}
                  </ul>
                </Card>
              </section>
            ))}
          </div>
        )}
      </PageBody>
    </>
  );
}


type Group = {
  key: string;
  label: string;
  items: ApiNavigatorMemoriesList["items"];
};


function groupByProject(
  items: ApiNavigatorMemoriesList["items"],
): Group[] {
  const buckets = new Map<string, Group>();
  for (const m of items) {
    const key = m.project_native_id ?? "__untagged__";
    const label = m.project_native_id ?? "General (no project tag)";
    if (!buckets.has(key)) {
      buckets.set(key, { key, label, items: [] });
    }
    buckets.get(key)!.items.push(m);
  }
  const out = Array.from(buckets.values());
  out.sort((a, b) => {
    if (a.key === "__untagged__") return 1;
    if (b.key === "__untagged__") return -1;
    return a.label.localeCompare(b.label);
  });
  return out;
}
