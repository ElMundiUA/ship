"use server";

/**
 * Server actions for `/knowledge/[id]` — upload flow.
 *
 * The upload card is a client component (it needs a file input and
 * pending state), but the actual network call runs here so we keep
 * the SHIP PAT server-side. The client only hands us a FormData
 * payload; this action decodes it, calls ``uploadToBucket``, and
 * tells Next.js to invalidate the current bucket page so the
 * "Recent runs" card and "Articles" card refresh with the new
 * run/article inline.
 */

import { revalidatePath } from "next/cache";

import {
  ApiHttpError,
  type ApiDistillOut,
  type ApiDistillerClassifier,
  syncConnectorBucket,
  uploadToBucket,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

// Mirrors the backend cap in ``POST /v1/.../upload``. We double-check
// on this side so the user gets a crisp error *before* we round-trip
// a megabyte of bytes through the action boundary.
const MAX_UPLOAD_BYTES = 1_000_000;

const ALLOWED_EXTS = new Set([
  ".md",
  ".markdown",
  ".txt",
  ".json",
  ".csv",
  ".html",
  ".htm",
  ".xml",
  ".css",
  ".js",
  ".mjs",
  ".cjs",
  ".ts",
  ".tsx",
  ".jsx",
  ".yaml",
  ".yml",
  ".toml",
  ".ini",
  ".cfg",
  ".log",
]);
const ALLOWED_MIME_PREFIXES = [
  "text/plain",
  "text/markdown",
  "text/x-markdown",
  "text/html",
  "text/css",
  "text/javascript",
  "text/xml",
  "text/csv",
  "text/yaml",
  "application/json",
  "application/javascript",
  "application/xml",
  "application/x-yaml",
];

export type UploadActionResult =
  | {
      ok: true;
      message: string;
      decision: ApiDistillOut["decision"];
      classifier: ApiDistillOut["classifier"];
      runId: string;
      articleIds: string[];
    }
  | {
      ok: false;
      message: string;
      /** HTTP status if the backend rejected the request. */
      status?: number;
    };

function sniffExtension(filename: string): string {
  const i = filename.lastIndexOf(".");
  return i >= 0 ? filename.slice(i).toLowerCase() : "";
}

function validateClientSide(file: File): string | null {
  if (file.size === 0) {
    return "Please choose a non-empty file.";
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return `File is too large (${file.size.toLocaleString()} bytes). Max is ${MAX_UPLOAD_BYTES.toLocaleString()}.`;
  }
  const ext = sniffExtension(file.name);
  const mimeOk =
    file.type === "" ||
    ALLOWED_MIME_PREFIXES.some((prefix) => file.type.startsWith(prefix));
  if (!mimeOk && !ALLOWED_EXTS.has(ext)) {
    return `Unsupported file type ${file.type || ext || "(unknown)"}. Use UTF-8 text or markup (md, json, csv, html, ts, yaml, …).`;
  }
  return null;
}

function coerceClassifier(raw: FormDataEntryValue | null): ApiDistillerClassifier {
  const value = typeof raw === "string" ? raw.toLowerCase() : "";
  if (value === "stub" || value === "llm" || value === "auto") {
    return value;
  }
  return "auto";
}

/**
 * Upload a text/markdown file to the given bucket slug.
 *
 * Call shape: `(workspaceId, slug, formData)` — the client invokes
 * this via `action={uploadBucketFileAction.bind(null, wsId, slug)}`.
 *
 * Always returns a plain object (never throws) so the client-side
 * `useFormState` hook can surface the error text inline.
 */
export async function uploadBucketFileAction(
  workspaceId: string,
  slug: string,
  formData: FormData,
): Promise<UploadActionResult> {
  const file = formData.get("file");
  if (!(file instanceof File)) {
    return { ok: false, message: "No file was attached." };
  }

  const localError = validateClientSide(file);
  if (localError !== null) {
    return { ok: false, message: localError };
  }

  const classifier = coerceClassifier(formData.get("classifier"));

  try {
    const result = await uploadToBucket(
      workspaceId,
      slug,
      { file, filename: file.name, classifier },
    );
    // Refresh the detail page so the articles + runs cards pick up
    // the new row. ``revalidatePath`` is safe to call regardless of
    // whether the cache actually held anything for this path.
    revalidatePath(`/knowledge/${slug}`);
    return {
      ok: true,
      message:
        result.decision === "skip"
          ? result.reason || "Already ingested — skipped."
          : result.decision === "update"
            ? "Article updated (new version)."
            : "Article created.",
      decision: result.decision,
      classifier: result.classifier,
      runId: result.run.id,
      articleIds: result.article_ids,
    };
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
    const msg = err instanceof Error ? err.message : String(err);
    return { ok: false, message: msg };
  }
}

export type SyncActionResult =
  | {
      ok: true;
      message: string;
      decision: ApiDistillOut["decision"];
      runId: string;
      articleIds: string[];
    }
  | { ok: false; message: string; status?: number };

/**
 * Phase 7b — refresh a connector-proxy bucket from its stored
 * ``source_ref``. Kicks the backend ``/sync`` route, then revalidates
 * the page so the Distiller runs + articles cards redraw with the
 * fresh row. Uses a server action (instead of a plain client fetch)
 * so the Ship PAT stays server-side.
 */
export async function syncConnectorBucketAction(
  workspaceId: string,
  slug: string,
): Promise<SyncActionResult> {
  try {
    const token = await getSessionToken();
    if (!token) {
      return { ok: false, message: "Not signed in — reload to re-auth." };
    }
    const result = await syncConnectorBucket(workspaceId, slug, token);
    revalidatePath(`/knowledge/${slug}`);
    return {
      ok: true,
      message:
        result.decision === "skip"
          ? result.reason || "Already up to date — skipped."
          : result.decision === "update"
            ? "Connector article updated."
            : "Connector article created.",
      decision: result.decision,
      runId: result.run.id,
      articleIds: result.article_ids,
    };
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
