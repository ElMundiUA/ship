"use client";

/**
 * LinearDefaultTeamPicker — surface ELS-71 part 2 on /settings/integrations.
 *
 * Shows the workspace's currently-selected default Linear team and lets
 * an operator change it from the team list discovered during OAuth probe.
 * Pre-fix the Console had no UI for the default — operators couldn't
 * tell which team ``create_ticket`` would land in, or fix it without
 * DB surgery.
 *
 * State model
 * -----------
 *
 * Reads ``team_id`` / ``team_key`` / ``team_options`` from the row's
 * ``config`` (passed in as props). Edits are optimistic locally (state
 * updates as soon as the POST returns ok); the parent server-component
 * re-fetches on next page render to surface anything the merge changed
 * server-side.
 */

import { useState } from "react";

export interface LinearTeamOption {
  id: string;
  key: string;
  name: string;
}

export function LinearDefaultTeamPicker({
  workspaceId,
  currentTeamId,
  currentTeamKey,
  teamOptions,
}: {
  workspaceId: string;
  currentTeamId: string | null;
  currentTeamKey: string | null;
  teamOptions: LinearTeamOption[];
}) {
  const [editing, setEditing] = useState(false);
  const [savedTeamId, setSavedTeamId] = useState<string | null>(currentTeamId);
  const [savedTeamKey, setSavedTeamKey] = useState<string | null>(currentTeamKey);
  const [draftId, setDraftId] = useState<string>(currentTeamId ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const current = teamOptions.find((t) => t.id === savedTeamId)
    ?? teamOptions.find((t) => t.key === savedTeamKey)
    ?? null;

  // No team_options means OAuth probe never ran (or cleared the
  // list). Show a hint instead of an empty dropdown so the operator
  // knows to re-auth, not "click harder".
  if (teamOptions.length === 0) {
    return (
      <p className="mt-1 text-[11px] text-amber-200">
        Default team unknown — re-run Linear OAuth so Ship can fetch
        your team list.
      </p>
    );
  }

  // Single team in the workspace — no picker needed; just show it.
  if (teamOptions.length === 1 && !editing) {
    const only = teamOptions[0];
    return (
      <p className="mt-1 text-[11px] text-white/55">
        Default team:{" "}
        <strong className="text-white">
          {only.name} ({only.key})
        </strong>{" "}
        <span className="text-white/35">— only team in this workspace</span>
      </p>
    );
  }

  if (!editing) {
    return (
      <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-white/55">
        <span>
          Default team:{" "}
          {current ? (
            <strong className="text-white">
              {current.name} ({current.key})
            </strong>
          ) : (
            <em className="text-amber-200">not picked</em>
          )}
        </span>
        <button
          type="button"
          data-testid="linear-default-team-edit"
          onClick={() => {
            setEditing(true);
            setError(null);
          }}
          className="cursor-pointer text-aqua/85 hover:text-aqua underline-offset-2 hover:underline"
        >
          {current ? "Change" : "Pick"}
        </button>
      </div>
    );
  }

  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
      <select
        value={draftId}
        disabled={saving}
        onChange={(e) => setDraftId(e.target.value)}
        className="rounded border border-white/10 bg-white/[0.04] px-2 py-1 text-white"
        data-testid="linear-default-team-select"
      >
        <option value="">— pick a team —</option>
        {teamOptions.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name} ({t.key})
          </option>
        ))}
      </select>
      <button
        type="button"
        disabled={saving || draftId === "" || draftId === savedTeamId}
        onClick={async () => {
          const picked = teamOptions.find((t) => t.id === draftId);
          if (!picked) return;
          setSaving(true);
          setError(null);
          try {
            const resp = await fetch("/api/integrations/linear-default-team", {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                workspace_id: workspaceId,
                team_id: picked.id,
                team_key: picked.key,
              }),
            });
            if (!resp.ok) {
              const payload = await resp.json().catch(() => ({}));
              throw new Error(
                typeof payload?.error === "string"
                  ? payload.error
                  : `HTTP ${resp.status}`,
              );
            }
            setSavedTeamId(picked.id);
            setSavedTeamKey(picked.key);
            setEditing(false);
          } catch (err) {
            setError(err instanceof Error ? err.message : "save failed");
          } finally {
            setSaving(false);
          }
        }}
        data-testid="linear-default-team-save"
        className="rounded-full border border-aqua/40 bg-aqua/10 px-2 py-0.5 font-semibold text-aqua hover:bg-aqua/20 disabled:opacity-40"
      >
        {saving ? "Saving…" : "Save"}
      </button>
      <button
        type="button"
        disabled={saving}
        onClick={() => {
          setDraftId(savedTeamId ?? "");
          setEditing(false);
          setError(null);
        }}
        className="text-white/55 hover:text-white"
      >
        Cancel
      </button>
      {error && <span className="text-coral">Couldn&apos;t save ({error}).</span>}
    </div>
  );
}
