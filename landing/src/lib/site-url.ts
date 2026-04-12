const DEFAULT_DEV = "http://localhost:3000";

function stripControlChars(input: string): string {
  return input.replace(/[\u0000-\u001F\u007F-\u009F]/g, "").trim();
}

/**
 * Safe base URL for Next.js `metadataBase`.
 * Empty env, host-only values, invisible chars, or typos must not take down the whole app.
 */
export function resolveMetadataBase(): URL {
  const raw = stripControlChars(process.env.NEXT_PUBLIC_SITE_URL ?? "");
  if (!raw) {
    return new URL(DEFAULT_DEV);
  }
  let candidate = raw;
  if (!/^https?:\/\//i.test(candidate)) {
    candidate = `https://${candidate}`;
  }
  try {
    const u = new URL(candidate);
    if (u.protocol !== "http:" && u.protocol !== "https:") {
      return new URL(DEFAULT_DEV);
    }
    if (!u.hostname || u.hostname.length > 253) {
      return new URL(DEFAULT_DEV);
    }
    return u;
  } catch {
    return new URL(DEFAULT_DEV);
  }
}
