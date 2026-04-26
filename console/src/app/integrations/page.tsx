import { redirect } from "next/navigation";

import { parseWorkspaceIdParam } from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

/**
 * Integrations live under Workspace settings. This URL is kept for
 * bookmarks, form POST returns, and deep links. Query params (e.g. errors)
 * are preserved.
 */
export default async function IntegrationsRedirectPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = (await (searchParams ?? Promise.resolve({}))) as Record<
    string,
    string | string[] | undefined
  >;
  const q = new URLSearchParams();
  for (const [key, val] of Object.entries(params)) {
    if (key === "tab") continue;
    const s = typeof val === "string" ? val : Array.isArray(val) ? val[0] : undefined;
    if (s) q.set(key, s);
  }
  q.set("tab", "integrations");
  const ws = parseWorkspaceIdParam(params.ws);
  if (ws) q.set("ws", ws);
  redirect(`/settings?${q.toString()}`);
}
