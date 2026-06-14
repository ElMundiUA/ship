/**
 * `/oauth/authorize` — MCP OAuth consent screen (ELS-296).
 *
 * The human-facing half of Ship's OAuth broker. An MCP client (Claude
 * Code / Desktop) sends the operator here after Dynamic Client
 * Registration; the `(authed)` layout bounces them through the normal
 * console login first if needed. They pick the workspace to grant and
 * approve — the grant route mints a single-use PKCE-bound code and 302s
 * the browser back to the client's loopback `redirect_uri`. Deny bounces
 * back with `error=access_denied`.
 *
 * No token is ever pasted: add the server URL → log in → grant → done.
 */

import { redirect } from "next/navigation";

import { PageBody, PageHeader } from "@/components/app-shell";
import { ApiHttpError, describeOAuthClient } from "@/lib/api/client";
import { getCachedSessionToken } from "@/lib/api/session-cache.server";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

function str(v: string | string[] | undefined): string {
  return typeof v === "string" ? v : "";
}

export default async function OAuthAuthorizePage({
  searchParams,
}: {
  searchParams?: Promise<SearchParams>;
}) {
  const params = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as SearchParams;

  const clientId = str(params.client_id);
  const redirectUri = str(params.redirect_uri);
  const codeChallenge = str(params.code_challenge);
  const codeChallengeMethod = str(params.code_challenge_method) || "S256";
  const state = str(params.state);
  const scope = str(params.scope);
  const responseType = str(params.response_type) || "code";

  const token = await getCachedSessionToken();
  if (!token) {
    const here = `/oauth/authorize?${new URLSearchParams(
      Object.entries(params).flatMap(([k, v]) =>
        typeof v === "string" ? [[k, v]] : [],
      ) as [string, string][],
    ).toString()}`;
    redirect(`/login?next=${encodeURIComponent(here)}&reason=session_expired`);
  }

  if (!clientId || !redirectUri || !codeChallenge || responseType !== "code") {
    return <ConsentError reason="This authorization request is incomplete or unsupported." />;
  }
  if (codeChallengeMethod !== "S256") {
    return <ConsentError reason="This client must use PKCE with S256." />;
  }

  let client: Awaited<ReturnType<typeof describeOAuthClient>>;
  try {
    client = await describeOAuthClient(clientId, token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 404) {
      return <ConsentError reason="Unknown client — re-add the Ship MCP server in your agent." />;
    }
    return <ConsentError reason="Couldn't load the client right now. Try again in a moment." />;
  }

  // Defense-in-depth: the grant endpoint re-validates, but reject an
  // off-list redirect here so we never render Approve for a bad URI.
  if (!redirectUriAllowed(client.redirect_uris, redirectUri)) {
    return <ConsentError reason="The redirect URL does not match this client's registration." />;
  }

  const clientName = client.client_name?.trim() || "An MCP client";

  return (
    <>
      <PageHeader kicker="authorize" title="Connect your agent" />
      <PageBody>
        <div className="mx-auto w-full max-w-lg">
          <section
            data-testid="oauth-consent"
            className="rounded-2xl border border-aqua/30 bg-aqua/[0.05] p-6"
          >
            <h2 className="text-lg font-semibold text-white">
              <span className="text-aqua">{clientName}</span> wants to operate
              Ship on your behalf
            </h2>
            <p className="mt-2 text-sm text-white/70">
              It will act as <b className="text-white/85">you</b> through the
              MCP edge — planning, tickets, reviews, and approvals — across{" "}
              <b className="text-white/85">all your workspaces</b>, including
              creating new ones. Hard-destructive actions still require a typed
              confirm in the console. You can revoke this any time in Settings →
              Agents &amp; access.
            </p>

            <form action="/api/oauth/grant" method="POST" className="mt-5 space-y-4">
              <input type="hidden" name="client_id" value={clientId} />
              <input type="hidden" name="redirect_uri" value={redirectUri} />
              <input type="hidden" name="code_challenge" value={codeChallenge} />
              <input
                type="hidden"
                name="code_challenge_method"
                value={codeChallengeMethod}
              />
              <input type="hidden" name="state" value={state} />
              <input type="hidden" name="scope" value={scope} />

              <div className="flex items-center gap-3 pt-1">
                <button
                  type="submit"
                  name="decision"
                  value="approve"
                  data-testid="oauth-approve"
                  className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
                >
                  Approve &amp; connect
                </button>
                <button
                  type="submit"
                  name="decision"
                  value="deny"
                  data-testid="oauth-deny"
                  className="rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-white/70 transition hover:text-white"
                >
                  Deny
                </button>
              </div>
            </form>
          </section>
          <p className="mt-3 text-center text-[11px] text-white/40">
            Requested by a client registered at{" "}
            <code className="font-mono">{hostOf(redirectUri)}</code>
          </p>
        </div>
      </PageBody>
    </>
  );
}

function ConsentError({ reason }: { reason: string }) {
  return (
    <>
      <PageHeader kicker="authorize" title="Connect your agent" />
      <PageBody>
        <div className="mx-auto w-full max-w-lg">
          <div
            data-testid="oauth-consent-error"
            className="rounded-2xl border border-coral/30 bg-coral/[0.06] p-6 text-sm text-coral/95"
          >
            {reason}
          </div>
        </div>
      </PageBody>
    </>
  );
}

function hostOf(uri: string): string {
  try {
    return new URL(uri).host;
  } catch {
    return uri;
  }
}

function isLoopback(uri: string): boolean {
  try {
    const h = new URL(uri).hostname.toLowerCase();
    return h === "127.0.0.1" || h === "localhost" || h === "::1";
  } catch {
    return false;
  }
}

/** Mirror of the backend's _redirect_uri_allowed: exact match, except
 * loopback URIs match port-insensitively (RFC 8252 §7.3). */
function redirectUriAllowed(registered: string[], requested: string): boolean {
  if (registered.includes(requested)) return true;
  if (!isLoopback(requested)) return false;
  let req: URL;
  try {
    req = new URL(requested);
  } catch {
    return false;
  }
  return registered.some((reg) => {
    if (!isLoopback(reg)) return false;
    try {
      const r = new URL(reg);
      return (
        r.protocol === req.protocol &&
        r.hostname === req.hostname &&
        r.pathname === req.pathname
      );
    } catch {
      return false;
    }
  });
}
