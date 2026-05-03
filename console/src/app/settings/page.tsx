import { redirect } from "next/navigation";

import { parseWorkspaceIdParam } from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

/**
 * `/settings` was a 969-line tabbed mega-page (Sprint C of the console
 * redesign). It now redirects to the canonical "general" tab — every
 * tab is its own route under /settings/{tab} and shares a layout in
 * src/app/settings/layout.tsx. Legacy `?tab=` query strings still get
 * routed through the SettingsShell on the per-tab pages.
 */
export default async function SettingsIndex({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as Record<
    string,
    string | string[] | undefined
  >;
  const requestedRaw = typeof params.tab === "string" ? params.tab : null;
  const aliased =
    requestedRaw === "tokens"
      ? "api-keys"
      : requestedRaw === "repos"
        ? "registries"
        : requestedRaw === "catalog"
          ? "general"
          : requestedRaw;
  const allowed = new Set([
    "general",
    "repositories",
    "registries",
    "members",
    "integrations",
    "agent-roles",
    "api-keys",
    "danger",
  ]);
  const tab = aliased && allowed.has(aliased) ? aliased : "general";

  const ws = parseWorkspaceIdParam(params.ws);
  const q = new URLSearchParams();
  if (ws) q.set("ws", ws);
  // Forward error / flag params so the destination tab can render them.
  for (const key of ["error", "invited", "invite_error", "resent", "revoked", "just_minted"] as const) {
    const v = params[key];
    const s = typeof v === "string" ? v : Array.isArray(v) ? v[0] : undefined;
    if (s) q.set(key, s);
  }
  const qs = q.toString();
  redirect(`/settings/${tab}${qs ? `?${qs}` : ""}`);
}
