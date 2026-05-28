"use server";

/**
 * Server actions for /knowledge importer management.
 *
 * Operator-driven: add a Lighthouse importer scoped to the workspace
 * (sitemap, RSS, URL list, GitHub repo, etc.), trigger an on-demand
 * run, and revalidate the page so the new row shows up.
 */

import { revalidatePath } from "next/cache";

import {
  ApiHttpError,
  type ApiImporterCreateBody,
  type ApiImporterDiscoveredItem,
  type ApiWorkspaceImporter,
  createWorkspaceImporter,
  discoverImporterItems,
  listWorkspaces,
  runWorkspaceImporter,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export type CreateImporterResult =
  | { ok: true; importer: ApiWorkspaceImporter }
  | { ok: false; message: string; status?: number };

export type RunImporterResult =
  | { ok: true; importerId: string }
  | { ok: false; message: string; status?: number };

export type DiscoverItemsResult =
  | { ok: true; items: ApiImporterDiscoveredItem[] }
  | { ok: false; message: string; status?: number };


async function requireToken(): Promise<string> {
  const token = await getSessionToken();
  if (!token) {
    throw new Error("Not signed in — reload the page to re-auth.");
  }
  return token;
}


async function requireWorkspaceId(token: string): Promise<string> {
  const wss = await listWorkspaces(token);
  if (wss.length === 0) {
    throw new Error("No workspace available — finish onboarding first.");
  }
  return wss[0].id;
}


export async function createImporterAction(
  body: ApiImporterCreateBody,
): Promise<CreateImporterResult> {
  if (!body.type?.trim()) return { ok: false, message: "Type is required." };
  if (!body.name?.trim()) return { ok: false, message: "Name is required." };

  let token: string;
  let workspaceId: string;
  try {
    token = await requireToken();
    workspaceId = await requireWorkspaceId(token);
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }

  try {
    const importer = await createWorkspaceImporter(workspaceId, body, token);
    revalidatePath("/knowledge");
    return { ok: true, importer };
  } catch (err) {
    return importerActionError(err);
  }
}


export async function discoverItemsAction(input: {
  type: string;
  config: Record<string, unknown>;
  secrets?: Record<string, string>;
  use_workspace_integration?: boolean;
}): Promise<DiscoverItemsResult> {
  if (!input.type?.trim()) return { ok: false, message: "Type is required." };

  let token: string;
  let workspaceId: string;
  try {
    token = await requireToken();
    workspaceId = await requireWorkspaceId(token);
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }

  try {
    const items = await discoverImporterItems(workspaceId, input, token);
    return { ok: true, items };
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return { ok: false, message: apiErrorMessage(err), status: err.status };
    }
    return { ok: false, message: apiErrorMessage(err) };
  }
}


export async function runImporterAction(
  importerId: string,
): Promise<RunImporterResult> {
  if (!importerId) return { ok: false, message: "Importer id is required." };

  let token: string;
  let workspaceId: string;
  try {
    token = await requireToken();
    workspaceId = await requireWorkspaceId(token);
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }

  try {
    await runWorkspaceImporter(workspaceId, importerId, token);
    revalidatePath("/knowledge");
    return { ok: true, importerId };
  } catch (err) {
    return importerActionError(err) as RunImporterResult;
  }
}


function importerActionError(err: unknown): CreateImporterResult {
  if (err instanceof ApiHttpError) {
    return { ok: false, message: apiErrorMessage(err), status: err.status };
  }
  return { ok: false, message: apiErrorMessage(err) };
}


function apiErrorMessage(err: unknown): string {
  if (err instanceof ApiHttpError) {
    return typeof err.detail === "string"
      ? err.detail
      : err.detail && typeof err.detail === "object"
        ? JSON.stringify(err.detail)
        : err.message;
  }
  return err instanceof Error ? err.message : String(err);
}
