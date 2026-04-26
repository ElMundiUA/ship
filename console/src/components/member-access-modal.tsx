"use client";

import { useCallback, useEffect, useId, useState } from "react";

import { ButtonGhost } from "@/components/ui";
import { MEMBER_ROLES, specialistSummary, SPECIALIST_LANES } from "@/lib/member-access";
import type { ApiMember } from "@/lib/api/types";

type Props = {
  member: ApiMember;
  workspaceId: string;
};

/**
 * Compact table cell: one click opens a modal with role + specialist forms
 * (native POST, same handlers as before — no change to API routes).
 */
export function MemberAccessModal({ member, workspaceId }: Props) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const slugs = member.answer_specialist_slugs ?? [];
  const all = slugs.includes("*");
  const summary = specialistSummary(slugs);

  const onClose = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (open) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = prev;
      };
    }
  }, [open]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="group flex w-full min-w-0 max-w-[14rem] flex-col items-stretch gap-0.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-left transition hover:border-aqua/35 hover:bg-white/[0.05]"
        aria-label={`Edit access for ${member.display_name || member.email}`}
      >
        <span className="text-xs font-semibold capitalize text-white">{member.role}</span>
        <span className="text-[10px] text-white/50">{summary}</span>
        <span className="pt-0.5 text-[9px] font-bold uppercase tracking-widest text-aqua/80 group-hover:text-aqua">
          Edit access
        </span>
      </button>

      {open ? (
        <div
          className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-4 backdrop-blur-sm sm:items-center"
          onClick={onClose}
          role="presentation"
        >
          <div
            className="max-h-[min(90vh,32rem)] w-full max-w-md overflow-y-auto rounded-2xl border border-white/10 bg-ink/95 p-5 shadow-2xl shadow-black/50"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
          >
            <div className="mb-4 flex items-start justify-between gap-2">
              <div className="min-w-0">
                <h2 id={titleId} className="font-display text-lg font-bold text-white">
                  Access
                </h2>
                <p className="mt-0.5 truncate text-sm text-white/60">
                  {member.display_name || member.email}
                </p>
                <p className="text-[11px] text-white/40">{member.email}</p>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="shrink-0 rounded-md p-1 text-lg leading-none text-white/45 transition hover:bg-white/10 hover:text-white"
                aria-label="Close"
              >
                ×
              </button>
            </div>

            <div className="space-y-5">
              <div>
                <h3 className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/45">
                  Role
                </h3>
                <form
                  action="/api/members/role"
                  method="POST"
                  className="flex flex-col gap-2 sm:flex-row sm:items-end"
                >
                  <input type="hidden" name="ws" value={workspaceId} />
                  <input type="hidden" name="member" value={member.id} />
                  <label className="block min-w-0 flex-1">
                    <span className="mb-1 block text-[10px] text-white/50">
                      Workspace role
                    </span>
                    <select
                      name="role"
                      defaultValue={member.role}
                      className="w-full rounded-lg border border-white/15 bg-white/[0.06] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
                    >
                      {MEMBER_ROLES.map((role) => (
                        <option key={role} value={role}>
                          {role}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="submit"
                    className="rounded-full bg-aqua/80 px-4 py-2 text-xs font-bold text-ink transition hover:bg-aqua"
                  >
                    Update role
                  </button>
                </form>
              </div>

              <div>
                <h3 className="mb-1 text-[10px] font-bold uppercase tracking-widest text-white/45">
                  Inbox / specialist lanes
                </h3>
                <p className="mb-3 text-[11px] leading-snug text-white/50">
                  Who can take BA, QA, and other specialist work. Owners can
                  cover all lanes or narrow below.
                </p>
                <form action="/api/members/specialists" method="POST" className="space-y-3">
                  <input type="hidden" name="ws" value={workspaceId} />
                  <input type="hidden" name="member" value={member.id} />
                  {member.role === "owner" ? (
                    <input type="hidden" name="is_owner" value="1" />
                  ) : null}
                  {member.role === "owner" ? (
                    <label className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white/85">
                      <input
                        type="checkbox"
                        name="all_specialist"
                        value="1"
                        defaultChecked={all}
                      />
                      All specialist types
                    </label>
                  ) : null}
                  <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                    {SPECIALIST_LANES.map(({ id, label }) => (
                      <li key={id}>
                        <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-white/10 bg-white/[0.02] px-2.5 py-1.5 text-xs text-white/75 transition hover:border-white/20">
                          <input
                            type="checkbox"
                            name={`sp_${id}`}
                            value="1"
                            defaultChecked={!all && slugs.includes(id)}
                            disabled={all && member.role === "owner"}
                            className="shrink-0"
                          />
                          {label}
                        </label>
                      </li>
                    ))}
                  </ul>
                  <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
                    <ButtonGhost type="button" onClick={onClose} className="!text-xs">
                      Cancel
                    </ButtonGhost>
                    <button
                      type="submit"
                      className="rounded-full bg-aqua/80 px-4 py-2 text-xs font-bold text-ink transition hover:bg-aqua"
                    >
                      Save lanes
                    </button>
                  </div>
                </form>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
