"use client";

import { useRef, useState } from "react";

import type { ApiWorkspace } from "@/lib/api/types";

export function WorkspaceNameField({ workspace }: { workspace: ApiWorkspace }) {
  const [editing, setEditing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="block">
      <div className="mb-1 text-[10px] font-bold uppercase tracking-widest text-white/45">
        Workspace name
      </div>

      {editing ? (
        <form
          action="/api/settings/workspace/rename"
          method="POST"
          className="flex items-center gap-2"
        >
          <input type="hidden" name="ws" value={workspace.id} />
          <input
            ref={inputRef}
            name="name"
            defaultValue={workspace.name}
            required
            maxLength={200}
            autoFocus
            className="flex-1 rounded-lg border border-aqua/40 bg-white/[0.06] px-3 py-2 text-sm text-white outline-none focus:border-aqua/60"
          />
          <button
            type="submit"
            className="rounded-md bg-aqua/15 px-3 py-2 text-xs font-semibold text-aqua transition hover:bg-aqua/25"
          >
            Save
          </button>
          <button
            type="button"
            onClick={() => setEditing(false)}
            className="rounded-md px-3 py-2 text-xs font-semibold text-white/45 transition hover:text-white"
          >
            Cancel
          </button>
        </form>
      ) : (
        <div className="flex items-center gap-2">
          <div className="flex-1 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-sm text-white/75">
            {workspace.name}
          </div>
          <button
            type="button"
            aria-label="Rename workspace"
            onClick={() => setEditing(true)}
            className="rounded-md p-2 text-white/35 transition hover:bg-white/[0.06] hover:text-white/80"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
            </svg>
          </button>
        </div>
      )}
    </div>
  );
}
