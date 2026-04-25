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
  listWorkspaces,
  syncConnectorBucket,
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

const GUIDED_IMPORT_MAX_BUCKETS = 20;

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
