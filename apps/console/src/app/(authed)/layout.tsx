import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { AppShellChrome } from "@/components/app-shell";
import { ApiUnavailable } from "@/components/api-unavailable";
import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
} from "@/lib/api/client";
import {
  getCachedMe,
  getCachedSessionToken,
  getCachedWorkspaces,
  meToShellUser,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import {
  pickWorkspace,
  toAppShellWorkspaces,
} from "@/lib/workspace-scope";

/**
 * Shell for every authed console route.
 *
 * Owns the session check, workspace listing, and `/auth/me` fetch — so
 * navigating between sibling routes (e.g. /inbox → /knowledge) does
 * not re-mount the sidebar nor re-issue those calls. Pages render only
 * their own data + a {@link PageHeader}.
 *
 * Pages still receive `searchParams` and can read `?ws=` themselves
 * via {@link getResolvedWorkspaceId}; the cookie middleware sets it,
 * so this layout's resolve is just to render the right name/slug in
 * the sidebar.
 */
export default async function AuthedLayout({
  children,
}: {
  children: ReactNode;
}) {
  if (!isApiConfigured()) {
    return (
      <AppShellChrome>
        <ApiUnavailable
          scope="console"
          details="SHIP_API_URL is not set on this deployment."
        />
      </AppShellChrome>
    );
  }

  const token = await getCachedSessionToken();
  if (!token) {
    redirect("/login?reason=session_expired");
  }

  let workspaces;
  try {
    workspaces = await getCachedWorkspaces();
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?reason=session_expired");
    }
    const reason =
      err instanceof ApiUnavailableError || err instanceof Error
        ? err.message
        : "Could not load workspaces.";
    return (
      <AppShellChrome>
        <ApiUnavailable scope="console" details={reason} />
      </AppShellChrome>
    );
  }

  if (workspaces.length === 0) {
    redirect("/onboarding?step=github");
  }

  const resolvedId = await getResolvedWorkspaceId({}, workspaces);
  const workspace = pickWorkspace(workspaces, resolvedId);
  const me = await getCachedMe();

  return (
    <AppShellChrome
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      allWorkspaces={toAppShellWorkspaces(workspaces)}
      me={meToShellUser(me)}
    >
      {children}
    </AppShellChrome>
  );
}
