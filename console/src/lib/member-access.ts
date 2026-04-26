import type { ApiMemberRole } from "@/lib/api/types";

export const MEMBER_ROLES: readonly ApiMemberRole[] = [
  "owner",
  "admin",
  "maintainer",
  "member",
  "viewer",
];

export const SPECIALIST_LANES: { id: string; label: string }[] = [
  { id: "ba", label: "BA" },
  { id: "qa", label: "QA" },
  { id: "eng", label: "Eng" },
  { id: "sec", label: "Sec" },
  { id: "pm", label: "PM" },
  { id: "dev", label: "Dev" },
];

/**
 * One-line label for the table / modal trigger (no raw slugs in the table row).
 */
export function specialistSummary(slugs: string[] | undefined): string {
  const list = slugs ?? [];
  if (list.includes("*")) return "All lanes";
  const picked = SPECIALIST_LANES.filter((l) => list.includes(l.id));
  if (picked.length === 0) return "None";
  return picked.map((p) => p.label).join(" · ");
}
