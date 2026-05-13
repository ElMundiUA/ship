/**
 * Telegram bind landing — opened from a Telegram bot DM after the
 * group admin types ``/link``. Reads the signed nonce from the
 * query string, shows what chat is about to be bound, and asks the
 * user to pick which Ship workspace to attach it to.
 *
 * The submit posts to ``/api/integrations/telegram/bind`` (POST
 * proxy that forwards to backend ``/v1/integrations/telegram/bind/confirm``).
 */

import { redirect } from "next/navigation";
import Link from "next/link";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listWorkspaces,
  previewTelegramBind,
  type ApiTelegramBindPreview,
} from "@/lib/api/client";
import type { ApiWorkspace } from "@/lib/api/types";

export const dynamic = "force-dynamic";

type SearchParams = Promise<{ [key: string]: string | string[] | undefined }>;

function pick(raw: string | string[] | undefined): string | undefined {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return v ?? undefined;
}

const ERROR_COPY: Record<string, string> = {
  expired: "This bind link has expired (links are valid for 10 minutes). Run /link in the Telegram group again.",
  invalid: "This bind link is invalid. Run /link in the Telegram group again.",
  api_unavailable: "The Ship backend isn't reachable right now. Try again in a moment.",
  forbidden: "You aren't an admin of the workspace you picked. Pick a workspace where you have admin or owner role.",
  unknown: "Something went wrong. Try again.",
};

export default async function TelegramBindPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const params = await searchParams;
  const nonce = pick(params.nonce);
  const errorCode = pick(params.error);

  if (!nonce) {
    return (
      <Frame>
        <h1 className="font-display text-2xl font-bold text-coral">
          Missing bind nonce
        </h1>
        <p className="mt-3 text-white/65">
          Open the link the Telegram bot sent you — it looks like{" "}
          <code className="rounded bg-white/10 px-1">
            /integrations/telegram/bind?nonce=…
          </code>
        </p>
      </Frame>
    );
  }

  if (!isApiConfigured()) {
    return <ErrorFrame message={ERROR_COPY.api_unavailable} />;
  }

  let preview: ApiTelegramBindPreview | null = null;
  let workspaces: ApiWorkspace[] = [];
  let loadError: string | null = null;
  try {
    [preview, workspaces] = await Promise.all([
      previewTelegramBind(nonce),
      listWorkspaces(),
    ]);
  } catch (err) {
    if (err instanceof ApiUnavailableError) loadError = "api_unavailable";
    else if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        // Force login then bounce back to this exact bind URL.
        const next = `/integrations/telegram/bind?nonce=${encodeURIComponent(nonce)}`;
        redirect(`/login?next=${encodeURIComponent(next)}`);
      }
      if (err.status === 400) loadError = "invalid";
      else if (err.status === 410) loadError = "expired";
      else loadError = `http_${err.status}`;
    } else loadError = "unknown";
  }

  const errorMessage = errorCode
    ? ERROR_COPY[errorCode] ?? "Couldn't bind — try again."
    : loadError
      ? ERROR_COPY[loadError] ?? "We couldn't verify that link."
      : null;

  if (preview === null) {
    return <ErrorFrame message={errorMessage ?? ERROR_COPY.unknown} />;
  }

  const expires = new Date(preview.expires_at);
  const minutesLeft = Math.max(
    0,
    Math.round((expires.getTime() - Date.now()) / 60_000),
  );

  return (
    <Frame>
      <h1 className="font-display text-2xl font-bold text-white">
        Bind Telegram group to a workspace
      </h1>
      <p className="mt-3 text-sm text-white/75">
        You&rsquo;re about to give Navigator access to the Telegram chat below.
        Anyone in that group will be able to talk to Navigator on behalf of
        the chosen workspace.
      </p>

      <div className="mt-6 rounded-2xl border border-white/15 bg-white/[0.04] p-4">
        <div className="text-xs uppercase tracking-wide text-white/50">
          Telegram chat
        </div>
        <div className="mt-1 font-display text-lg font-semibold text-white">
          {preview.chat_title ?? "(untitled group)"}
        </div>
        <div className="mt-1 font-mono text-xs text-white/55">
          chat_id {preview.chat_id}
        </div>
        {preview.already_bound_workspace_id && (
          <div className="mt-3 rounded-xl border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-xs text-amber-100">
            ⚠️ This chat is already bound to another workspace. Confirming
            will move it — the previous workspace&rsquo;s bot session will
            be revoked.
          </div>
        )}
        <div className="mt-2 text-[11px] text-white/40">
          Link expires in ≈ {minutesLeft} min.
        </div>
      </div>

      {workspaces.length === 0 ? (
        <div className="mt-6 rounded-xl border border-coral/40 bg-coral/10 px-4 py-3 text-xs text-white/85">
          You aren&rsquo;t a member of any workspace yet. Create one in
          Console first, then come back to this link.
        </div>
      ) : (
        <form
          action="/api/integrations/telegram/bind"
          method="POST"
          className="mt-6 flex flex-col gap-3"
        >
          <input type="hidden" name="nonce" value={nonce} />
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-white/70">Workspace</span>
            <select
              name="workspace_id"
              required
              defaultValue=""
              className="rounded-xl border border-white/20 bg-ink/60 px-3 py-2 text-sm text-white focus:border-aqua/60 focus:outline-none"
            >
              <option value="" disabled>
                Pick a workspace…
              </option>
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
            <span className="text-[11px] text-white/45">
              You must be admin or owner of the workspace.
            </span>
          </label>
          <div className="mt-2 flex items-center gap-3">
            <button
              type="submit"
              className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
            >
              Bind workspace →
            </button>
            <Link href="/" className="text-xs text-white/55 hover:text-white">
              Cancel
            </Link>
          </div>
        </form>
      )}

      {errorMessage && (
        <div className="mt-6 rounded-xl border border-coral/40 bg-coral/10 px-4 py-3 text-xs text-white/85">
          {errorMessage}
        </div>
      )}
    </Frame>
  );
}

function ErrorFrame({ message }: { message: string }) {
  return (
    <Frame>
      <h1 className="font-display text-2xl font-bold text-coral">
        Bind link unusable
      </h1>
      <p className="mt-3 text-white/75">{message}</p>
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
