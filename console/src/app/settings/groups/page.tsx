import { redirect } from "next/navigation";

import { parseWorkspaceIdParam } from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

/** Operational groups UI was retired — use member specialist lanes under Settings → Members. */
export default async function GroupsSettingsRedirect({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = (await (searchParams ?? Promise.resolve({}))) as Record<
    string,
    string | string[] | undefined
  >;
  const ws = parseWorkspaceIdParam(params.ws);
  const q = new URLSearchParams();
  q.set("tab", "members");
  if (ws) q.set("ws", ws);
  redirect(`/settings?${q.toString()}`);
}
