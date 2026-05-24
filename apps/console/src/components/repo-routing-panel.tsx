/**
 * Settings → Connected code: dispatch routing panel (Variant A).
 *
 * Server component. Shows which repo each project's agent runs land in:
 * a workspace default + per-project overrides. Unbound projects use the
 * default; with no default, a ticket files a clarification instead of
 * dispatching to an arbitrary repo. Plain ``<form>`` POSTs to
 * ``/api/repo-routing`` so it works with JS off.
 */

import { Card, CardHeader } from "@/components/ui";
import {
  getPriorities,
  getRepoRouting,
  type ApiActivatedRepo,
  type ApiRepoRouting,
} from "@/lib/api/client";

const SELECT_CLASS =
  "rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40";
const SAVE_CLASS =
  "rounded-full border border-white/15 bg-white/[0.06] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:bg-white/[0.12]";

export async function RepoRoutingPanel({
  workspaceId,
  repositories,
}: {
  workspaceId: string;
  repositories: ApiActivatedRepo[];
}) {
  const activated = repositories.filter((r) => r.activated_at);

  let routing: ApiRepoRouting | null = null;
  try {
    routing = await getRepoRouting(workspaceId);
  } catch {
    routing = null;
  }

  let projects: { project_native_id: string; name: string }[] = [];
  try {
    const pr = await getPriorities(workspaceId);
    projects = (pr.projects ?? [])
      .filter((p) => p.priority_state !== "done")
      .map((p) => ({
        project_native_id: p.project_native_id,
        name: p.name,
      }));
  } catch {
    projects = [];
  }

  const boundRepoByProject = new Map(
    (routing?.bindings ?? []).map((b) => [b.project_native_id, b.repo_id]),
  );

  return (
    <Card>
      <CardHeader
        title="Dispatch routing"
        subtitle="Which repo each project's agent runs land in. Unbound projects use the workspace default; with no default set, a ticket files a clarification instead of dispatching to a random repo."
      />

      {activated.length === 0 ? (
        <p className="text-sm text-white/55">
          No activated repos yet — connect a repo first.
        </p>
      ) : (
        <div className="space-y-6">
          {/* Workspace default */}
          <form
            action="/api/repo-routing"
            method="POST"
            className="flex flex-wrap items-center gap-3"
          >
            <input type="hidden" name="ws" value={workspaceId} />
            <input type="hidden" name="action" value="set-default" />
            <span className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/45">
              Default repo
            </span>
            <select
              name="repo_id"
              defaultValue={routing?.default_repo_id ?? ""}
              className={SELECT_CLASS}
            >
              <option value="">— none (require explicit binding) —</option>
              {activated.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.full_name}
                </option>
              ))}
            </select>
            <button type="submit" className={SAVE_CLASS}>
              Save default
            </button>
          </form>

          {/* Per-project overrides */}
          {projects.length === 0 ? (
            <p className="text-sm text-white/45">
              No projects yet — bindings appear here once the workspace has
              tracker projects.
            </p>
          ) : (
            <div className="space-y-2">
              <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/45">
                Per-project overrides
              </p>
              <ul className="divide-y divide-white/[0.06]">
                {projects.map((p) => (
                  <li
                    key={p.project_native_id}
                    className="flex flex-wrap items-center justify-between gap-3 py-2.5"
                  >
                    <span className="min-w-0 truncate text-sm text-white/85">
                      {p.name}
                    </span>
                    <form
                      action="/api/repo-routing"
                      method="POST"
                      className="flex items-center gap-2"
                    >
                      <input type="hidden" name="ws" value={workspaceId} />
                      <input type="hidden" name="action" value="bind" />
                      <input
                        type="hidden"
                        name="project_native_id"
                        value={p.project_native_id}
                      />
                      <select
                        name="repo_id"
                        defaultValue={
                          boundRepoByProject.get(p.project_native_id) ?? ""
                        }
                        className={SELECT_CLASS}
                      >
                        <option value="">Use default</option>
                        {activated.map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.full_name}
                          </option>
                        ))}
                      </select>
                      <button type="submit" className={SAVE_CLASS}>
                        Save
                      </button>
                    </form>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
