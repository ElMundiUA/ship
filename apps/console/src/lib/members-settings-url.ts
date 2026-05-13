/**
 * Members UI moved under Workspace settings; API form POSTs redirect here.
 */
export function workspaceMembersSettingsUrl(
  origin: string,
  workspaceId: string | undefined,
  extra?: Record<string, string>,
): URL {
  const url = new URL("/settings", origin);
  url.searchParams.set("tab", "members");
  if (workspaceId) url.searchParams.set("ws", workspaceId);
  if (extra) {
    for (const [k, v] of Object.entries(extra)) {
      if (v) url.searchParams.set(k, v);
    }
  }
  return url;
}
