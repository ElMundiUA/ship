"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";

import { Badge, ButtonDanger, ButtonGhost, ButtonPrimary, Card } from "@/components/ui";
import type {
  ApiAgentRole,
  ApiAgentRoleDefault,
  ApiAgentRoleDetail,
} from "@/lib/api/client";

/**
 * Client island for /settings/agent-roles.
 *
 * Two stacked sections:
 *
 * - **Workspace customs** — overrides + clones authored in this
 *   workspace. Each row exposes Edit and Delete; delete is a soft
 *   confirm because clones may still be referenced by routines.
 * - **Ship defaults** — file-backed read-only catalogue. Each row
 *   exposes "Override" (creates a same-slug workspace row, body
 *   pre-filled from the default) and "Clone as new" (slug picker
 *   modal). When a default is already shadowed by an override, the
 *   row is muted and the override's edit lives in the customs
 *   section above.
 *
 * Editing happens in a single side-panel that opens for any row
 * (override / clone / override-from-default). All saves round-trip
 * through ``/api/agent-roles[/slug]`` and we refetch via
 * ``router.refresh()`` so SSR re-derives ship_default vs override
 * deterministically.
 */

type Mode =
  | { kind: "closed" }
  | {
      kind: "edit";
      slug: string;
      name: string;
      prompt: string;
      isCustom: boolean; // true → PUT existing row; false → POST override
      baseRoleSlug: string | null;
    }
  | {
      kind: "clone";
      slug: string; // editable
      name: string;
      prompt: string;
      baseRoleSlug: string;
    };

export function AgentRolesList({
  workspaceId,
  defaults,
  customs,
}: {
  workspaceId: string;
  defaults: ApiAgentRoleDefault[];
  customs: ApiAgentRole[];
}) {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>({ kind: "closed" });
  const [error, setError] = useState<string | null>(null);
  const [isSaving, startSaving] = useTransition();

  const customBySlug = useMemo(() => {
    const m = new Map<string, ApiAgentRole>();
    for (const c of customs) m.set(c.slug, c);
    return m;
  }, [customs]);

  const overrides = customs.filter((c) => c.base_role_slug == null);
  const clones = customs.filter((c) => c.base_role_slug != null);

  async function loadAndEdit(slug: string, isCustom: boolean) {
    setError(null);
    try {
      const qs = isCustom
        ? new URLSearchParams({ workspaceId }).toString()
        : "defaultOnly=1";
      const res = await fetch(
        `/api/agent-roles/${encodeURIComponent(slug)}?${qs}`,
      );
      if (!res.ok) throw await readError(res);
      const row = (await res.json()) as ApiAgentRoleDetail;
      const base = defaults.find((d) => d.slug === slug)?.slug ?? null;
      setMode({
        kind: "edit",
        slug,
        name: row.name,
        prompt: row.prompt,
        isCustom,
        baseRoleSlug: isCustom
          ? row.base_role_slug ?? null
          : base,
      });
    } catch (err) {
      setError(
        err instanceof Error
          ? `Couldn't load role body: ${err.message}`
          : "Couldn't load role body.",
      );
    }
  }

  function startClone(d: ApiAgentRoleDefault) {
    setError(null);
    // Pre-fill the body by fetching the default detail; UX-wise we
    // resolve the body lazily on save so the modal opens snappily.
    setMode({
      kind: "clone",
      slug: `${d.slug}-custom`,
      name: `${d.name} (custom)`,
      prompt: "",
      baseRoleSlug: d.slug,
    });
  }

  async function save() {
    if (mode.kind === "closed") return;
    setError(null);
    startSaving(async () => {
      try {
        if (mode.kind === "edit" && mode.isCustom) {
          // PUT existing workspace row.
          const res = await fetch(
            `/api/agent-roles/${encodeURIComponent(mode.slug)}`,
            {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                workspaceId,
                name: mode.name,
                prompt: mode.prompt,
              }),
            },
          );
          if (!res.ok) throw await readError(res);
        } else {
          // POST a new row — either an override (slug shadows
          // default, base_role_slug=null) or a clone (custom slug,
          // base_role_slug set).
          const isOverride =
            mode.kind === "edit" && !mode.isCustom; // edit-of-default = override path
          const slug = isOverride ? mode.slug : (mode as { slug: string }).slug;
          const baseSlug = isOverride ? null : (mode as { baseRoleSlug: string }).baseRoleSlug;

          // For clone: pull default body as starter when prompt empty.
          let prompt = mode.prompt;
          if (mode.kind === "clone" && !prompt.trim()) {
            const res = await fetch(
              `/api/agent-roles/${encodeURIComponent(mode.baseRoleSlug)}?defaultOnly=1`,
            );
            if (res.ok) {
              const def = (await res.json()) as ApiAgentRoleDetail;
              prompt = def.prompt;
            }
          }

          const res = await fetch(`/api/agent-roles`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              workspaceId,
              slug,
              name: mode.name,
              prompt,
              base_role_slug: baseSlug,
            }),
          });
          if (!res.ok) throw await readError(res);
        }
        setMode({ kind: "closed" });
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    });
  }

  async function remove(slug: string) {
    setError(null);
    if (
      !window.confirm(
        `Delete this workspace agent role (${slug})? Routines pointing at it will fail until rewired.`,
      )
    ) {
      return;
    }
    startSaving(async () => {
      try {
        const res = await fetch(
          `/api/agent-roles/${encodeURIComponent(slug)}?` +
            new URLSearchParams({ workspaceId }).toString(),
          { method: "DELETE" },
        );
        if (!res.ok && res.status !== 204) throw await readError(res);
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    });
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
          {error}
        </div>
      )}

      {/* ── Workspace customs ───────────────────────────────────── */}
      <section>
        <h2 className="mb-2 font-display text-sm font-bold uppercase tracking-[0.18em] text-white/55">
          Workspace overrides &amp; clones
        </h2>
        {customs.length === 0 ? (
          <Card>
            <p className="text-xs text-white/55">
              No custom agent roles yet. Defaults below are used as-is
              for every routine in this workspace.
            </p>
          </Card>
        ) : (
          <div className="space-y-2">
            {[...overrides, ...clones].map((row) => (
              <Card key={row.id} className="flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <code className="font-mono text-xs text-white/85">
                      {row.slug}
                    </code>
                    {row.base_role_slug ? (
                      <Badge tone="info">
                        Cloned from {row.base_role_slug}
                      </Badge>
                    ) : (
                      <Badge tone="workspace">Override</Badge>
                    )}
                  </div>
                  <div className="mt-0.5 text-sm text-white/85">{row.name}</div>
                </div>
                <ButtonGhost onClick={() => loadAndEdit(row.slug, true)}>
                  Edit
                </ButtonGhost>
                <ButtonDanger onClick={() => remove(row.slug)}>
                  Delete
                </ButtonDanger>
              </Card>
            ))}
          </div>
        )}
      </section>

      {/* ── Ship defaults ───────────────────────────────────────── */}
      <section>
        <h2 className="mb-2 font-display text-sm font-bold uppercase tracking-[0.18em] text-white/55">
          Ship defaults
        </h2>
        <div className="space-y-2">
          {defaults.map((d) => {
            const overridden = customBySlug.get(d.slug);
            return (
              <Card
                key={d.slug}
                className={`flex items-center gap-3 ${
                  overridden ? "opacity-60" : ""
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <code className="font-mono text-xs text-white/85">
                      {d.slug}
                    </code>
                    {d.fsm_stage && (
                      <Badge tone="neutral">FSM: {d.fsm_stage}</Badge>
                    )}
                    {overridden && (
                      <Badge tone="warn">Overridden in workspace</Badge>
                    )}
                  </div>
                  <div className="mt-0.5 text-sm text-white/85">{d.name}</div>
                </div>
                {overridden ? (
                  <ButtonGhost
                    onClick={() => loadAndEdit(d.slug, true)}
                  >
                    Edit override
                  </ButtonGhost>
                ) : (
                  <ButtonGhost onClick={() => loadAndEdit(d.slug, false)}>
                    Override
                  </ButtonGhost>
                )}
                <ButtonGhost onClick={() => startClone(d)}>
                  Clone as new
                </ButtonGhost>
              </Card>
            );
          })}
        </div>
      </section>

      {/* ── Editor side panel ───────────────────────────────────── */}
      {mode.kind !== "closed" && (
        <Card className="space-y-3">
          <div>
            <h3 className="font-display text-base font-bold text-white">
              {mode.kind === "clone"
                ? `Clone "${mode.baseRoleSlug}"`
                : mode.isCustom
                  ? `Edit "${mode.slug}"`
                  : `Override Ship default "${mode.slug}"`}
            </h3>
            {mode.kind === "edit" && !mode.isCustom && (
              <p className="text-xs text-white/55">
                Saves a workspace override that shadows the Ship default
                for this workspace. Delete the row to revert.
              </p>
            )}
            {mode.kind === "clone" && (
              <p className="text-xs text-white/55">
                Creates a new workspace row with its own slug. Routines
                pointing at this slug will load this body.
              </p>
            )}
          </div>

          {mode.kind === "clone" && (
            <label className="block">
              <span className="text-[11px] uppercase tracking-[0.18em] text-white/45">
                Slug
              </span>
              <input
                type="text"
                value={mode.slug}
                onChange={(e) =>
                  setMode({ ...mode, slug: e.target.value.trim() })
                }
                className="mt-1 w-full rounded-md border border-white/15 bg-zinc-950 px-2 py-1 font-mono text-sm text-white"
                placeholder="developer-mobile"
              />
            </label>
          )}

          <label className="block">
            <span className="text-[11px] uppercase tracking-[0.18em] text-white/45">
              Display name
            </span>
            <input
              type="text"
              value={mode.name}
              onChange={(e) => setMode({ ...mode, name: e.target.value })}
              className="mt-1 w-full rounded-md border border-white/15 bg-zinc-950 px-2 py-1 text-sm text-white"
            />
          </label>

          <label className="block">
            <span className="text-[11px] uppercase tracking-[0.18em] text-white/45">
              Prompt body (markdown)
            </span>
            <textarea
              value={mode.prompt}
              onChange={(e) => setMode({ ...mode, prompt: e.target.value })}
              rows={20}
              className="mt-1 w-full rounded-md border border-white/15 bg-zinc-950 px-2 py-2 font-mono text-xs text-white"
              placeholder={
                mode.kind === "clone"
                  ? "Leave empty to seed from the source default."
                  : ""
              }
            />
          </label>

          <div className="flex justify-end gap-2">
            <ButtonGhost onClick={() => setMode({ kind: "closed" })}>
              Cancel
            </ButtonGhost>
            <ButtonPrimary onClick={save}>
              {isSaving ? "Saving…" : "Save"}
            </ButtonPrimary>
          </div>
        </Card>
      )}
    </div>
  );
}

async function readError(res: Response): Promise<Error> {
  try {
    const body = (await res.json()) as { error?: string };
    return new Error(body.error ?? `HTTP ${res.status}`);
  } catch {
    return new Error(`HTTP ${res.status}`);
  }
}
