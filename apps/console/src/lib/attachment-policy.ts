/**
 * Client mirror of ``backend.app.services.attachments.policy``.
 * Keep extension map and resolve logic in sync with the Python module.
 */

export const ALLOWED_MIME_TYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/gif",
  "application/pdf",
  "text/markdown",
  "text/plain",
  "text/csv",
  "application/json",
  "application/yaml",
  "application/x-yaml",
]);

const GENERIC_MIMES = new Set(["", "application/octet-stream"]);

export const EXTENSION_TO_MIME: Record<string, string> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  gif: "image/gif",
  pdf: "application/pdf",
  md: "text/markdown",
  txt: "text/plain",
  csv: "text/csv",
  json: "application/json",
  yaml: "application/yaml",
  yml: "application/yaml",
};

function extensionMime(filename: string): string | null {
  const dot = filename.lastIndexOf(".");
  if (dot < 0) return null;
  const ext = filename.slice(dot + 1).toLowerCase();
  return EXTENSION_TO_MIME[ext] ?? null;
}

export function resolveMime(
  filename: string,
  reportedMime: string | undefined | null,
): string {
  const mime = (reportedMime ?? "").trim();
  if (ALLOWED_MIME_TYPES.has(mime)) {
    return mime;
  }
  const inferred = extensionMime(filename);
  if (GENERIC_MIMES.has(mime) || !ALLOWED_MIME_TYPES.has(mime)) {
    if (inferred !== null) {
      return inferred;
    }
  }
  return mime || "application/octet-stream";
}

export function isAllowedAttachment(
  filename: string,
  reportedMime: string | undefined | null,
): boolean {
  return ALLOWED_MIME_TYPES.has(resolveMime(filename, reportedMime));
}
