"use server";

/**
 * Server actions for `/knowledge` — bucket creation entry points.
 *
 * Kept in its own file rather than co-located with the page so the
 * "use server" boundary is crisp: the page is a server component,
 * the new-connector dialog is a client component, and this module
 * is the minimal surface both sides agree on.
 */

import { redirect } from "next/navigation";

import {
  ApiHttpError,
  createBucket,
  createConnectorBucket,
  createKnowledgeImportSource,
  listWorkspaces,
  syncKnowledgeImportSource,
  syncConnectorBucket,
  updateBucket,
} from "@/lib/api/client";
import type { ApiBucketScope } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";

export type CreateBucketResult =
  | { ok: true; slug: string }
  | { ok: false; message: string; status?: number };

type Input = {
  kind: "external_static" | "connector_proxy";
  name: string;
  slug?: string;
  description?: string;
  purpose?: string;
  bucketType?: string;
  authority?: string;
  accessLevel?: string;
  freshnessPolicy?: string;
  scope?: ApiBucketScope;
  repoId?: string | null;
  projectId?: string | null;
  integrationId?: string;
  /** JSON blob the user pastes ("database_id": "..."). Free-form per connector. */
  resourceRef?: Record<string, unknown>;
};

export type GuidedImportItem = {
  title: string;
  slug?: string;
  description?: string;
  integrationId: string;
  resourceRef: Record<string, unknown>;
};

export type GuidedImportResult =
  | {
      ok: true;
      created: { slug: string; title: string; synced: boolean; syncError?: string }[];
    }
  | { ok: false; message: string; status?: number };

export type ImportSourceResult =
  | { ok: true; sourceId: string; synced: boolean; stats?: Record<string, unknown>; syncError?: string }
  | { ok: false; message: string; status?: number };

const GUIDED_IMPORT_MAX_BUCKETS = 20;

export type UpdateBucketResult =
  | { ok: true; slug: string }
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

/**
 * Create a fresh bucket from the `/knowledge` dialog.
 *
 * On success we ``redirect`` to the new bucket's detail page so the
 * operator can immediately upload content / hit "Sync now". On
 * validation errors we return a plain object with the backend's
 * message so the client component can surface it inline.
 *
 * `redirect()` throws internally — we catch `ApiHttpError` before it
 * runs so Next.js's control flow exception bubbles out cleanly.
 */
export async function createBucketAction(
  input: Input,
): Promise<CreateBucketResult> {
  const name = input.name.trim();
  if (name.length === 0) {
    return { ok: false, message: "Bucket name is required." };
  }

  let token: string;
  let workspaceId: string;
  try {
    token = await requireToken();
    workspaceId = await requireWorkspaceId(token);
  } catch (err) {
    return {
      ok: false,
      message: err instanceof Error ? err.message : String(err),
    };
  }

  const commonOpts = { token };
  let slug: string | null = null;
  try {
    if (input.kind === "connector_proxy") {
      if (!input.integrationId) {
        return {
          ok: false,
          message: "Pick an integration before saving the bucket.",
        };
      }
      const created = await createConnectorBucket(
        workspaceId,
        {
          name,
          slug: input.slug?.trim() || undefined,
          description: input.description?.trim() || undefined,
          integrationId: input.integrationId,
          resourceRef: input.resourceRef ?? {},
          scopeKind: input.scope ?? "workspace",
          repoId: input.repoId ?? null,
          projectId: input.projectId ?? null,
        },
        commonOpts,
      );
      slug = created.slug;
    } else {
      const created = await createBucket(
        workspaceId,
        {
          name,
          slug: input.slug?.trim() || undefined,
          description: input.description?.trim() || undefined,
          scope_kind: input.scope ?? "workspace",
          source_kind: "external_static",
          source_ref: knowledgeMetadata(input),
          repo_id: input.repoId ?? null,
          project_id: input.projectId ?? null,
        },
        commonOpts,
      );
      slug = created.slug;
    }
  } catch (err) {
    if (err instanceof ApiHttpError) {
      const detail =
        typeof err.detail === "string"
          ? err.detail
          : err.detail && typeof err.detail === "object"
            ? JSON.stringify(err.detail)
            : err.message;
      return { ok: false, message: detail, status: err.status };
    }
    return {
      ok: false,
      message: err instanceof Error ? err.message : String(err),
    };
  }

  if (!slug) {
    return { ok: false, message: "Bucket create returned no slug." };
  }

  // NOTE: `redirect()` throws an internal Next.js error that the
  // framework intercepts to actually perform the navigation. It
  // must be called *outside* a try/catch that swallows errors —
  // we're fine here because the surrounding try is only for the
  // create call, not this redirect.
  redirect(`/knowledge/${encodeURIComponent(slug)}`);
}

export async function updateBucketMetadataAction(input: {
  slug: string;
  name?: string;
  description?: string;
}): Promise<UpdateBucketResult> {
  const slug = input.slug.trim();
  if (!slug) return { ok: false, message: "Bucket slug is required." };

  let token: string;
  let workspaceId: string;
  try {
    token = await requireToken();
    workspaceId = await requireWorkspaceId(token);
  } catch (err) {
    return {
      ok: false,
      message: err instanceof Error ? err.message : String(err),
    };
  }

  try {
    await updateBucket(
      workspaceId,
      slug,
      {
        name: input.name?.trim() || undefined,
        description: input.description?.trim() || undefined,
      },
      { token },
    );
    return { ok: true, slug };
  } catch (err) {
    return bucketActionError(err);
  }
}

export async function archiveBucketAction(
  slug: string,
): Promise<UpdateBucketResult> {
  const cleanSlug = slug.trim();
  if (!cleanSlug) return { ok: false, message: "Bucket slug is required." };

  let token: string;
  let workspaceId: string;
  try {
    token = await requireToken();
    workspaceId = await requireWorkspaceId(token);
  } catch (err) {
    return {
      ok: false,
      message: err instanceof Error ? err.message : String(err),
    };
  }

  try {
    await updateBucket(workspaceId, cleanSlug, { archived: true }, { token });
    return { ok: true, slug: cleanSlug };
  } catch (err) {
    return bucketActionError(err);
  }
}

export async function createGuidedImportAction(
  input: {
    scope?: ApiBucketScope;
    items: GuidedImportItem[];
  },
): Promise<GuidedImportResult> {
  if (input.items.length === 0) {
    return { ok: false, message: "Select at least one page to import." };
  }
  if (input.items.length > GUIDED_IMPORT_MAX_BUCKETS) {
    return {
      ok: false,
      message: `Guided import is capped at ${GUIDED_IMPORT_MAX_BUCKETS} buckets. Split this source into smaller valuable groups first.`,
    };
  }

  let token: string;
  let workspaceId: string;
  try {
    token = await requireToken();
    workspaceId = await requireWorkspaceId(token);
  } catch (err) {
    return {
      ok: false,
      message: err instanceof Error ? err.message : String(err),
    };
  }

  const created: { slug: string; title: string; synced: boolean; syncError?: string }[] = [];
  for (const item of input.items) {
    const title = item.title.trim();
    if (!title) return { ok: false, message: "Every import row needs a title." };
    if (!item.integrationId) {
      return { ok: false, message: "Every import row needs an integration." };
    }
    try {
      const bucket = await createConnectorBucket(
        workspaceId,
        {
          name: title,
          slug: item.slug?.trim() || undefined,
          description: item.description?.trim() || undefined,
          integrationId: item.integrationId,
          resourceRef: item.resourceRef,
          scopeKind: input.scope ?? "workspace",
        },
        { token },
      );
      try {
        await syncConnectorBucket(workspaceId, bucket.slug, token);
        created.push({ slug: bucket.slug, title, synced: true });
      } catch (syncErr) {
        const syncError =
          syncErr instanceof ApiHttpError
            ? typeof syncErr.detail === "string"
              ? syncErr.detail
              : syncErr.message
            : syncErr instanceof Error
              ? syncErr.message
              : String(syncErr);
        created.push({ slug: bucket.slug, title, synced: false, syncError });
      }
    } catch (err) {
      if (err instanceof ApiHttpError) {
        const detail =
          typeof err.detail === "string"
            ? err.detail
            : err.detail && typeof err.detail === "object"
              ? JSON.stringify(err.detail)
              : err.message;
        return { ok: false, message: detail, status: err.status };
      }
      return {
        ok: false,
        message: err instanceof Error ? err.message : String(err),
      };
    }
  }
  return { ok: true, created };
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
    return {
      ok: false,
      message: err instanceof Error ? err.message : String(err),
    };
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
        sync_interval_minutes:
          input.kind === "static_upload" ? null : 24 * 60,
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
      return {
        ok: true,
        sourceId: source.id,
        synced: false,
        syncError: apiErrorMessage(err),
      };
    }
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return { ok: false, message: apiErrorMessage(err), status: err.status };
    }
    return { ok: false, message: apiErrorMessage(err) };
  }
}

function knowledgeMetadata(input: Input): Record<string, unknown> | null {
  const metadata = {
    purpose: input.purpose?.trim() || undefined,
    bucket_type: input.bucketType?.trim() || undefined,
    authority: input.authority?.trim() || undefined,
    access_level: input.accessLevel?.trim() || undefined,
    freshness_policy: input.freshnessPolicy?.trim() || undefined,
  };
  if (Object.values(metadata).every((value) => value === undefined)) {
    return null;
  }
  return { knowledge_metadata: metadata };
}

function bucketActionError(err: unknown): UpdateBucketResult {
  if (err instanceof ApiHttpError) {
    return { ok: false, message: apiErrorMessage(err), status: err.status };
  }
  return {
    ok: false,
    message: apiErrorMessage(err),
  };
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
