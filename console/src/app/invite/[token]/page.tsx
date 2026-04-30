/**
 * Closed-beta invite acceptance landing page (E08 T07).
 *
 * Public entry point for platform-level invites (closed-beta gating).
 * An unauthenticated invitee opens the URL and sees their invite status:
 * - pending → "You're invited" + Sign-in CTA
 * - accepted → "Already joined" message
 * - revoked/expired → "Request again" message
 * - not found → 404
 *
 * Sign-in CTA links to /login?next=/invite/[token] so the JIT-gate
 * can accept the invite as part of the auth flow.
 */

import Link from "next/link";
import { notFound } from "next/navigation";

import { ApiHttpError, ApiUnavailableError } from "@/lib/api/client";
import { apiFetch } from "@/lib/api/client";

export const dynamic = "force-dynamic";

type InviteStatusResponse = {
  email: string;
  expires_at: string;
  status: "pending" | "accepted" | "revoked" | "expired";
  accepted_at: string | null;
};

export default async function InviteTokenPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;

  let statusData: InviteStatusResponse | null = null;
  let fetchError: string | null = null;

  try {
    statusData = await apiFetch<InviteStatusResponse>(
      `/v1/public/invites/${encodeURIComponent(token)}`,
      { token: null }
    );
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      fetchError = "api_unavailable";
    } else if (err instanceof ApiHttpError) {
      if (err.status === 404) {
        notFound();
      } else {
        fetchError = `http_${err.status}`;
      }
    } else {
      fetchError = "unknown";
    }
  }

  if (fetchError) {
    return (
      <Frame>
        <h1 className="font-display text-2xl font-bold text-coral">
          Unable to verify invite
        </h1>
        <p className="mt-3 text-white/65">
          {fetchError === "api_unavailable"
            ? "The Ship backend isn't reachable right now. Try again in a moment."
            : "Something went wrong checking your invite. Try again or request a fresh invite."}
        </p>
        <div className="mt-6">
          <Link
            href="/getting-started"
            className="inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
          >
            Request access
          </Link>
        </div>
      </Frame>
    );
  }

  if (!statusData) {
    notFound();
  }

  const expiresDate = new Date(statusData.expires_at);
  const acceptedDate = statusData.accepted_at
    ? new Date(statusData.accepted_at)
    : null;

  if (statusData.status === "pending") {
    return (
      <Frame>
        <h1 className="font-display text-2xl font-bold text-white">
          You&rsquo;re invited to Ship&rsquo;s closed beta
        </h1>
        <p className="mt-3 text-sm text-white/75">
          This invitation is for{" "}
          <code className="rounded bg-white/10 px-1">{statusData.email}</code>.
        </p>
        <p className="mt-2 text-xs text-white/50">
          Expires{" "}
          {expiresDate.toLocaleString(undefined, {
            dateStyle: "medium",
            timeStyle: "short",
          })}
          .
        </p>
        <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center">
          <Link
            href={`/login?next=%2Finvite%2F${encodeURIComponent(token)}`}
            className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
          >
            Sign in to accept →
          </Link>
          <Link
            href="/getting-started"
            className="text-xs text-white/55 hover:text-white"
          >
            Cancel
          </Link>
        </div>
      </Frame>
    );
  }

  if (statusData.status === "accepted") {
    return (
      <Frame>
        <h1 className="font-display text-2xl font-bold text-white">
          Welcome to Ship
        </h1>
        <p className="mt-3 text-sm text-white/75">
          This invitation was already accepted on{" "}
          {acceptedDate?.toLocaleString(undefined, {
            dateStyle: "medium",
            timeStyle: "short",
          })}
          . Sign in normally to continue.
        </p>
        <div className="mt-6">
          <Link
            href="/login"
            className="inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
          >
            Sign in →
          </Link>
        </div>
      </Frame>
    );
  }

  if (statusData.status === "revoked" || statusData.status === "expired") {
    return (
      <Frame>
        <h1 className="font-display text-2xl font-bold text-coral">
          Invite is no longer valid
        </h1>
        <p className="mt-3 text-sm text-white/75">
          {statusData.status === "revoked"
            ? "This invitation was revoked. "
            : "This invitation has expired. "}
          Request a fresh invite to proceed.
        </p>
        <div className="mt-6">
          <Link
            href="/getting-started"
            className="inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
          >
            Request access →
          </Link>
        </div>
      </Frame>
    );
  }

  return notFound();
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
