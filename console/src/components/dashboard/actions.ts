"use server";

/**
 * Server actions for the Dashboard v2 prioritizer.
 *
 * The prioritizer is a client component (drag-and-drop, optimistic
 * state) but mutations have to be authenticated against the workspace
 * API with a server-only session token. These wrappers shave the
 * token out of the cookie, round-trip to the backend, and return a
 * typed result the client can render without reaching for raw
 * fetch().
 */

import { revalidatePath } from "next/cache";

import {
  ApiHttpError,
  type ApiPrioritiesResponse,
  type ApiPriorityState,
  type ApiReorderRow,
  reorderPriorities,
  setAutonomyPaused,
  setPriorityState,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export type PriorityActionResult =
  | { ok: true; payload: ApiPrioritiesResponse }
  | { ok: false; message: string; status?: number };

export type AutonomyActionResult =
  | { ok: true; paused: boolean }
  | { ok: false; message: string; status?: number };


async function requireToken(): Promise<string> {
  const token = await getSessionToken();
  if (!token) throw new Error("Not signed in — reload the page to re-auth.");
  return token;
}


function toError<T extends { ok: false; message: string; status?: number }>(
  err: unknown,
): T {
  if (err instanceof ApiHttpError) {
    const detail =
      typeof err.detail === "string" ? err.detail : err.message;
    return { ok: false, message: detail, status: err.status } as T;
  }
  return { ok: false, message: err instanceof Error ? err.message : String(err) } as T;
}


export async function reorderPrioritiesAction(
  workspaceId: string,
  order: ApiReorderRow[],
): Promise<PriorityActionResult> {
  if (!workspaceId) {
    return { ok: false, message: "workspaceId is required" };
  }
  let token: string;
  try {
    token = await requireToken();
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }
  try {
    const payload = await reorderPriorities(workspaceId, order, token);
    revalidatePath("/");
    return { ok: true, payload };
  } catch (err) {
    return toError(err);
  }
}


export async function setProjectStateAction(
  workspaceId: string,
  projectNativeId: string,
  state: ApiPriorityState,
): Promise<PriorityActionResult> {
  if (!workspaceId) {
    return { ok: false, message: "workspaceId is required" };
  }
  if (!projectNativeId) {
    return { ok: false, message: "projectNativeId is required" };
  }
  let token: string;
  try {
    token = await requireToken();
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }
  try {
    const payload = await setPriorityState(
      workspaceId,
      projectNativeId,
      state,
      token,
    );
    revalidatePath("/");
    return { ok: true, payload };
  } catch (err) {
    return toError(err);
  }
}


export async function setAutonomyPausedAction(
  workspaceId: string,
  paused: boolean,
): Promise<AutonomyActionResult> {
  if (!workspaceId) {
    return { ok: false, message: "workspaceId is required" };
  }
  let token: string;
  try {
    token = await requireToken();
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }
  try {
    const result = await setAutonomyPaused(workspaceId, paused, token);
    revalidatePath("/");
    return { ok: true, paused: result.autonomy_paused };
  } catch (err) {
    return toError(err);
  }
}
