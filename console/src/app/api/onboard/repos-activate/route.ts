/**
 * Onboarding step — persist repo activations from the picker.
 *
 * The form on the wizard's "Pick repos" step submits one ``repo_id``
 * field per ticked checkbox. We collect them, POST to the backend's
 * ``/v1/workspaces/{ws}/repos/activate`` endpoint, and bounce the user
 * to the next step (``tracker``). The backend treats the payload as a
 * complete replacement set, so unticking a previously-activated repo is
 * the same code path as adding a new one.
 *
 * We deliberately don't validate ids client-side beyond "non-empty
 * digits" — the backend already 422s on anything the GitHub App can't
 * see, and we surface that as a wizard error banner without leaking the
 * raw id list.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  activateRepos,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();

  if (!wsId) {
    return NextResponse.redirect(new URL("/onboarding", origin), 303);
  }

  if (!isApiConfigured()) {
    return wizardError(origin, wsId, "api_unavailable");
  }

  // ``getAll`` keeps every checkbox; the form sends one ``repo_id`` entry
  // per ticked row. Empty selection is a soft error — the user either
  // wants to skip (use the link) or made a mistake.
  const rawIds = form.getAll("repo_id").map((v) => v.toString().trim());
  const externalIds = rawIds
    .filter((s) => /^\d+$/.test(s))
    .map((s) => Number.parseInt(s, 10))
    .filter((n) => Number.isFinite(n) && n > 0);

  if (externalIds.length === 0) {
    return wizardError(origin, wsId, "empty");
  }

  // Preset is optional — if the user skipped the picker we fall back to
  // the backend's ``adoption-minimum`` default. Whitelist here so a
  // hand-crafted POST can't smuggle an unknown string through (the
  // backend re-validates too; this just gives a cleaner wizard error).
  const rawPreset = (form.get("preset") ?? "").toString().trim();
  const knownPresets = new Set([
    "web-app",
    "api-backend",
    "mobile-app",
    "cli",
    "monorepo",
    "marketing",
    "adoption-minimum",
  ]);
  const preset = knownPresets.has(rawPreset) ? rawPreset : null;

  try {
    await activateRepos(wsId, externalIds, { preset });
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return wizardError(origin, wsId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return wizardError(origin, wsId, "forbidden");
      if (err.status === 409) return wizardError(origin, wsId, "no_install");
      if (err.status === 502) return wizardError(origin, wsId, "bad_token");
      // 422 means the user picked an id the App can't see (race with an
      // uninstall, or someone toggled access on github.com between page
      // load and submit). Funnel them back to the picker.
      if (err.status === 422) return wizardError(origin, wsId, "unknown");
      return wizardError(origin, wsId, `http_${err.status}`);
    }
    return wizardError(origin, wsId, "unknown");
  }

  // Success → next step in the wizard. We deliberately don't pass the
  // activated count back as a query param; the next page can re-fetch
  // when it cares (and it doesn't yet).
  const next = new URL("/onboarding", origin);
  next.searchParams.set("step", "tracker");
  next.searchParams.set("ws", wsId);
  next.searchParams.set("repos", "wired");
  return NextResponse.redirect(next, 303);
}

function wizardError(origin: string, wsId: string, code: string) {
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "repos");
  url.searchParams.set("ws", wsId);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
