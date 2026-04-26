import { redirect } from "next/navigation";

import { parseWorkspaceIdParam } from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

/** @deprecated Use `/settings?tab=members` — kept for bookmarks. */
export default async function MembersRedirectPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const raw = (await (searchParams ?? Promise.resolve({}))) as Record<
    string,
    string | string[] | undefined
  >;
  const ws = parseWorkspaceIdParam(raw.ws);
  const q = new URLSearchParams();
  q.set("tab", "members");
  if (ws) q.set("ws", ws);
  for (const key of [
    "error",
    "invite_error",
    "invited",
    "resent",
  ] as const) {
    const v = raw[key];
    const s = typeof v === "string" ? v : Array.isArray(v) ? v[0] : undefined;
    if (s) q.set(key, s);
  }
  if (raw.revoked === "1") q.set("revoked", "1");
  redirect(`/settings?${q.toString()}`);
}
