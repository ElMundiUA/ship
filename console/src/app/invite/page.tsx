/**
 * Invite-accept landing page (B7).
 *
 * Public entry point — an unauthenticated invitee can open the URL
 * and see who's invited them, to what workspace, in what role, and
 * until when. The only action is "Accept", which posts to
 * ``/api/team/accept-invite``; the handler forces a login round-
 * trip if the visitor has no session yet.
 *
 * We intentionally *don't* auto-accept on page load — some invitees
 * want to confirm the workspace before joining, and the 403 "wrong
 * email" case is way easier to explain when it's a manual click.
 */

import Link from "next/link";

import { ApiHttpError, ApiUnavailableError, peekInvite } from "@/lib/api/client";

export const dynamic = "force-dynamic";

type SearchParams = Promise<{ [key: string]: string | string[] | undefined }>;

function pick(raw: string | string[] | undefined): string | undefined {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return v ?? undefined;
}

const ERROR_COPY: Record<string, string> = {
  wrong_email:
    "This invite was issued to a different email. Sign in with that address, or ask the admin for a fresh invite.",
  expired: "This invite has expired. Ask the admin to reissue it.",
  not_found: "Invite link not found — it may have been revoked.",
  api_unavailable:
    "The Ship backend isn't reachable right now. Try again in a moment.",
  unknown: "Something went wrong accepting the invite. Try again.",
};

export default async function InvitePage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const params = await searchParams;
  const token = pick(params.token);
  const errorCode = pick(params.error);

  if (!token) {
    return (
      <Frame>
        <h1 className="font-display text-2xl font-bold text-coral">
          Missing invite token
        </h1>
        <p className="mt-3 text-white/65">
          Open the invite link your teammate shared — it looks like{" "}
          <code className="rounded bg-white/10 px-1">
            /invite?token=…
          </code>
          .
        </p>
      </Frame>
    );
  }

  let peek: Awaited<ReturnType<typeof peekInvite>> | null = null;
  let peekError: string | null = null;
  try {
    peek = await peekInvite(token);
  } catch (err) {
    if (err instanceof ApiUnavailableError) peekError = "api_unavailable";
    else if (err instanceof ApiHttpError) {
      if (err.status === 404) peekError = "not_found";
      else if (err.status === 410) peekError = "expired";
      else peekError = `http_${err.status}`;
    } else peekError = "unknown";
  }

  const errorMessage = errorCode
    ? ERROR_COPY[errorCode] ?? "Couldn't accept the invite — try again."
    : peekError
      ? ERROR_COPY[peekError] ??
        "We couldn't verify that invite. Ask the admin for a fresh link."
      : null;

  return (
    <Frame>
      <h1 className="font-display text-2xl font-bold text-white">
        You're invited to Ship
      </h1>
      {peek ? (
        <>
          <p className="mt-3 text-sm text-white/75">
            <strong className="text-white">{peek.workspace_name}</strong> is
            inviting <code className="rounded bg-white/10 px-1">{peek.email}</code>{" "}
            to join as{" "}
            <span className="rounded bg-aqua/15 px-2 py-0.5 text-aqua">
              {peek.role}
            </span>
            .
          </p>
          <p className="mt-2 text-xs text-white/50">
            {peek.invited_by_email
              ? `Invited by ${peek.invited_by_email}. `
              : ""}
            Expires{" "}
            {new Date(peek.expires_at).toLocaleString(undefined, {
              dateStyle: "medium",
              timeStyle: "short",
            })}
            .
          </p>
          <form
            action="/api/team/accept-invite"
            method="POST"
            className="mt-6 flex flex-col gap-2 sm:flex-row sm:items-center"
          >
            <input type="hidden" name="token" value={token} />
            <button
              type="submit"
              className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
            >
              Accept invite →
            </button>
            <Link
              href="/"
              className="text-xs text-white/55 hover:text-white"
            >
              Not now
            </Link>
          </form>
        </>
      ) : null}

      {errorMessage && (
        <div className="mt-6 rounded-xl border border-coral/40 bg-coral/10 px-4 py-3 text-xs text-white/85">
          {errorMessage}
        </div>
      )}
    </Frame>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative min-h-screen overflow-hidden bg-ink text-white">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(60%_60%_at_15%_15%,rgba(255,107,107,0.18),transparent),radial-gradient(50%_50%_at_85%_15%,rgba(178,118,255,0.18),transparent),radial-gradient(70%_70%_at_60%_95%,rgba(118,255,217,0.15),transparent)]"
      />
      <main className="mx-auto w-full max-w-xl px-6 pt-24 pb-20">
        <Link
          href="/"
          className="mb-8 inline-flex items-center gap-2 font-display text-lg font-bold"
        >
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-coral via-lilac to-aqua text-ink">
            S
          </span>
          Ship
        </Link>
        {children}
      </main>
    </div>
  );
}
