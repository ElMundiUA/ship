/**
 * Browser-side fetch to the Ship API via the session-aware Next proxy.
 *
 * Client components must not import ``@/lib/api/client`` (server-only).
 */

export class ProxyHttpError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ProxyHttpError";
    this.status = status;
  }
}

export async function proxyApiFetch<T>(
  path: string,
  opts: { method?: string; body?: unknown } = {},
): Promise<T> {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  const res = await fetch(`/api/proxy${normalized}`, {
    method: opts.method ?? "GET",
    headers: {
      accept: "application/json",
      ...(opts.body !== undefined ? { "content-type": "application/json" } : {}),
    },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    cache: "no-store",
  });
  const text = await res.text();
  let data: unknown = null;
  if (text.length > 0) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : `Request failed (${res.status})`;
    throw new ProxyHttpError(res.status, detail);
  }
  return data as T;
}
