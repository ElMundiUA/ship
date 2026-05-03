/**
 * Linked Telegram groups for the current workspace.
 *
 * Lists every ``telegram_chat_link`` row for the workspace, with an
 * "Unlink" button per row that posts to
 * ``/api/integrations/telegram/unlink`` (which revokes the bot's
 * service PAT first so the bridge stops answering before the row
 * is removed). New links are created from inside Telegram by typing
 * ``/link`` in a group — there is no "Connect" button here on
 * purpose, since the binding has to start from the chat the bot is
 * actually in.
 */

import { redirect } from "next/navigation";
import Link from "next/link";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listTelegramLinks,
  listWorkspaces,
  type ApiTelegramLink,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace } from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

export const metadata = { title: "Telegram — Workspace settings" };

const ERROR_COPY: Record<string, string> = {
  bad_input: "Missing link id.",
  api_unavailable: "Backend isn't reachable right now.",
  unknown: "Something went wrong.",
};

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

function pick(raw: string | string[] | undefined): string | undefined {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return v ?? undefined;
}

export default async function TelegramSettingsPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const params = await searchParams;
  const errorCode = pick(params.error);
  const unlinked = pick(params.unlinked) === "1";
  const bound = pick(params.telegram_bound) === "1";

  if (!isApiConfigured()) {
    return <Frame><Empty msg="SHIP_API_URL is not set on this deployment." /></Frame>;
  }
  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fsettings%2Fintegrations%2Ftelegram&reason=session_expired");

  const workspaces = await listWorkspaces(token);
  if (workspaces.length === 0) redirect("/onboarding?step=github");

  const resolved = await getResolvedWorkspaceId(params, workspaces);
  if (workspaces.length > 1 && !resolved) redirect("/?next=/settings/integrations/telegram");
  const target = pickWorkspace(workspaces, resolved);

  let links: ApiTelegramLink[] = [];
  let loadError: string | null = null;
  try {
    links = await listTelegramLinks(target.id, token);
  } catch (err) {
    if (err instanceof ApiUnavailableError) loadError = "api_unavailable";
    else if (err instanceof ApiHttpError) loadError = `http_${err.status}`;
    else loadError = "unknown";
  }

  const errorMessage = errorCode
    ? ERROR_COPY[errorCode] ?? "Couldn't unlink — try again."
    : loadError
      ? ERROR_COPY[loadError] ?? `Couldn't load (${loadError}).`
      : null;

  return (
    <Frame>
      <div className="mb-6 flex items-baseline justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl font-bold text-white">Telegram</h1>
          <p className="mt-1 text-sm text-white/65">
            Bound groups for <span className="text-white/85">{target.name}</span>.
          </p>
        </div>
        <Link
          href={`/settings/integrations?ws=${target.id}`}
          className="text-xs text-white/55 hover:text-white"
        >
          ← All integrations
        </Link>
      </div>

      {bound && (
        <div className="mb-4 rounded-xl border border-aqua/40 bg-aqua/[0.08] px-4 py-3 text-xs text-white/85">
          ✅ Telegram group bound. The bot will respond in the chat now.
        </div>
      )}
      {unlinked && (
        <div className="mb-4 rounded-xl border border-white/15 bg-white/[0.05] px-4 py-3 text-xs text-white/75">
          Group unlinked.
        </div>
      )}
      {errorMessage && (
        <div className="mb-4 rounded-xl border border-coral/40 bg-coral/10 px-4 py-3 text-xs text-white/85">
          {errorMessage}
        </div>
      )}

      <div className="rounded-2xl border border-white/15 bg-white/[0.04] p-5">
        <h2 className="mb-2 font-display text-sm font-semibold text-white/85">
          How to add the bot
        </h2>
        <ol className="list-decimal pl-5 text-xs text-white/65 leading-relaxed">
          <li>Add the Ship bot to your Telegram group.</li>
          <li>An admin types <code className="rounded bg-white/10 px-1">/link</code> in the group.</li>
          <li>The bot DMs the admin a link back to this Console.</li>
          <li>Pick this workspace, confirm — the bot starts answering immediately.</li>
        </ol>
      </div>

      <div className="mt-6">
        <h2 className="mb-3 font-display text-sm font-semibold text-white/85">
          Linked groups ({links.length})
        </h2>
        {links.length === 0 ? (
          <Empty msg="No groups bound yet. Run /link in a Telegram group to start." />
        ) : (
          <ul className="flex flex-col gap-3">
            {links.map((l) => (
              <li
                key={l.id}
                className="flex items-center justify-between gap-4 rounded-2xl border border-white/15 bg-white/[0.04] p-4"
              >
                <div className="min-w-0">
                  <div className="truncate font-medium text-white">
                    {l.title ?? "(untitled group)"}
                  </div>
                  <div className="font-mono text-[11px] text-white/50">
                    chat_id {l.telegram_chat_id}
                    {!l.has_active_pat && (
                      <span className="ml-3 rounded bg-coral/20 px-1.5 py-0.5 text-coral">
                        token revoked
                      </span>
                    )}
                  </div>
                </div>
                <form
                  action="/api/integrations/telegram/unlink"
                  method="POST"
                  className="shrink-0"
                >
                  <input type="hidden" name="link_id" value={l.id} />
                  <input type="hidden" name="ws" value={target.id} />
                  <button
                    type="submit"
                    className="rounded-full border border-coral/40 bg-coral/10 px-3 py-1 text-xs font-semibold text-coral hover:bg-coral/20"
                  >
                    Unlink
                  </button>
                </form>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Frame>
  );
}

function Empty({ msg }: { msg: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.02] px-4 py-6 text-center text-xs text-white/55">
      {msg}
    </div>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-3xl px-6 py-12">{children}</main>
  );
}
