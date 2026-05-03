"use server";

/**
 * Server actions for `/knowledge`.
 *
 * Bucket creation is no longer a user surface — workspaces ship with
 * a fixed set of buckets and the harvester pipeline routes new content
 * into them. Operators can archive/restore buckets and connect
 * external import sources; the import-source feed lands in
 * ``Improvement(kind='knowledge_note')`` rows for the harvester to
 * pick up.
 */

import {
  ApiHttpError,
  archiveBucket,
  createKnowledgeImportSource,
  listWorkspaces,
  restoreBucket,
  syncKnowledgeImportSource,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export type ArchiveResult =
  | { ok: true; slug: string }
  | { ok: false; message: string; status?: number };

export type ImportSourceResult =
  | { ok: true; sourceId: string; synced: boolean; stats?: Record<string, unknown>; syncError?: string }
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

export async function archiveBucketAction(slug: string): Promise<ArchiveResult> {
  const cleanSlug = slug.trim();
  if (!cleanSlug) return { ok: false, message: "Bucket slug is required." };

  let token: string;
  let workspaceId: string;
  try {
    token = await requireToken();
    workspaceId = await requireWorkspaceId(token);
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }

  try {
    await archiveBucket(workspaceId, cleanSlug, { token });
    return { ok: true, slug: cleanSlug };
  } catch (err) {
    return bucketActionError(err);
  }
}

export async function restoreBucketAction(slug: string): Promise<ArchiveResult> {
  const cleanSlug = slug.trim();
  if (!cleanSlug) return { ok: false, message: "Bucket slug is required." };

  let token: string;
  let workspaceId: string;
  try {
    token = await requireToken();
    workspaceId = await requireWorkspaceId(token);
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }

  try {
    await restoreBucket(workspaceId, cleanSlug, { token });
    return { ok: true, slug: cleanSlug };
  } catch (err) {
    return bucketActionError(err);
  }
}

export async function createImportSourceAction(input: {
  kind: "notion" | "confluence" | "static_upload" | "docs_repo" | "website";
  name: string;
  config: Record<string, unknown>;
  integrationId?: string | null;
  repoId?: string | null;
  syncNow?: boolean;
}): Promise<ImportSourceResult> {
  const name = input.name.trim();
  if (!name) return { ok: false, message: "Source name is required." };

  let token: string;
  let workspaceId: string;
  try {
    token = await requireToken();
    workspaceId = await requireWorkspaceId(token);
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }

  try {
    const source = await createKnowledgeImportSource(
      workspaceId,
      {
        kind: input.kind,
        name,
        config: input.config,
        integration_id: input.integrationId ?? null,
        repo_id: input.repoId ?? null,
        sync_interval_minutes: input.kind === "static_upload" ? null : 24 * 60,
      },
      { token },
    );
    if (!input.syncNow) {
      return { ok: true, sourceId: source.id, synced: false };
    }
    try {
      const run = await syncKnowledgeImportSource(workspaceId, source.id, token);
      return { ok: true, sourceId: source.id, synced: true, stats: run.stats };
    } catch (err) {
      return { ok: true, sourceId: source.id, synced: false, syncError: apiErrorMessage(err) };
    }
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return { ok: false, message: apiErrorMessage(err), status: err.status };
    }
    return { ok: false, message: apiErrorMessage(err) };
  }
}

function bucketActionError(err: unknown): ArchiveResult {
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
