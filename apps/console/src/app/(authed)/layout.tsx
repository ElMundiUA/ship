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
import {
  getRequestPathname,
  resolveConsoleMode,
} from "@/lib/console-mode.server";
import { isPathAllowed } from "@/lib/console-mode";
import {
  getLayoutWorkspaceSearchParams,
  getResolvedWorkspaceId,
} from "@/lib/workspace-resolve.server";
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
 * Pages receive `searchParams` directly; this layout reads the same
 * ``?ws=`` via {@link getLayoutWorkspaceSearchParams} (middleware
 * forwards it on a request header) so the sidebar chip matches pages.
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

  const layoutSearch = await getLayoutWorkspaceSearchParams();
  const resolvedId = await getResolvedWorkspaceId(layoutSearch, workspaces);
  const workspace = pickWorkspace(workspaces, resolvedId);
  const me = await getCachedMe();

  // ELS-235 (strangler step 0): per-workspace console mode. Non-full
  // modes 302 disallowed paths to the operator hub; the /approve/{id}
  // confirm surface stays reachable in EVERY mode so pending operator
  // approvals are never orphaned.
  const consoleMode = await resolveConsoleMode(workspace.id, token);
  if (consoleMode !== "full") {
    const pathname = await getRequestPathname();
    if (!isPathAllowed(consoleMode, pathname)) {
      redirect("/");
    }
  }

  return (
    <AppShellChrome
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      allWorkspaces={toAppShellWorkspaces(workspaces)}
      me={meToShellUser(me)}
      consoleMode={consoleMode}
    >
      {children}
    </AppShellChrome>
  );
}
