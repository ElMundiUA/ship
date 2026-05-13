/**
 * One-shot server-side stash for freshly-minted invite accept URLs.
 *
 * The backend returns plaintext invite tokens exactly once — we can't
 * query them again later. To render the "copy this link" UI on the
 * ``/members`` page immediately after bulk-creating invites, we
 * persist the ``{invite_id: accept_url}`` map into an httpOnly,
 * short-TTL cookie on the redirect response. ``/members`` reads +
 * clears the cookie on the next render.
 *
 * Tokens in a cookie sit in the browser's cookie jar and ride along
 * with every subsequent request to our origin for the cookie's
 * lifetime. We keep the lifetime to 5 minutes and clear it the first
 * time the members page reads it so the window of exposure is
 * minimal. Good enough for the admin-adjacent WOW flow — not a
 * substitute for a purpose-built invites audit surface, which we'd
 * build if B7 graduates past the pilot.
 */

import { cookies } from "next/headers";

const STASH_COOKIE = "ship_invite_stash";
const MAX_AGE_SECONDS = 300;

export async function stashInviteTokens(
  map: Record<string, string>,
): Promise<void> {
  if (Object.keys(map).length === 0) return;
  const store = await cookies();
  store.set({
    name: STASH_COOKIE,
    value: encodeURIComponent(JSON.stringify(map)),
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: MAX_AGE_SECONDS,
  });
}

export async function consumeInviteTokens(): Promise<
  Record<string, string>
> {
  const store = await cookies();
  const raw = store.get(STASH_COOKIE)?.value;
  if (!raw) return {};
  try {
    const parsed = JSON.parse(decodeURIComponent(raw));
    if (parsed && typeof parsed === "object") {
      // Clear after read — one-shot.
      store.set({
        name: STASH_COOKIE,
        value: "",
        httpOnly: true,
        sameSite: "lax",
        path: "/",
        maxAge: 0,
      });
      return parsed as Record<string, string>;
    }
  } catch {
    /* ignored — corrupt cookie just means we render without URLs */
  }
  return {};
}
