/**
 * Resolves the public origin (`scheme://host[:port]`) for the *current* request.
 *
 * Why: when the console runs inside Docker, `request.url` carries the
 * container-internal binding (`http://0.0.0.0:3001/...`). If we redirect
 * back to the browser using that URL, the browser treats it as a different
 * origin from the one it loaded the form from (`http://localhost:3001`),
 * and silently drops the freshly-set session cookie. Always rebuilding
 * redirects against the request's `Host` (and forwarded scheme) keeps
 * cookies on the same origin the browser is using.
 */
export function resolveOrigin(request: Request): string {
  const headers = request.headers;
  const url = new URL(request.url);
  const host = headers.get("x-forwarded-host") ?? headers.get("host") ?? url.host;
  const proto =
    headers.get("x-forwarded-proto") ??
    (host.startsWith("localhost") || host.startsWith("127.") ? "http" : url.protocol.replace(":", ""));
  return `${proto}://${host}`;
}
