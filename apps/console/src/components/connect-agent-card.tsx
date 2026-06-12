"use client";

/**
 * "Connect your agent" — the console's new front door (ELS-288).
 *
 * Shows the MCP endpoint, mints a PAT in one click (existing
 * `/api/tokens/mint` route, JSON mode), and hands the operator
 * copy-ready attach commands for Claude Code and Claude Desktop.
 *
 * The minted secret lives in component state only — shown once, never
 * persisted client-side (matches the Settings one-time reveal rule).
 * Dismissal collapses the card to a one-line hint via localStorage
 * (`ship.connect-agent.dismissed.{wsId}`) — deliberately not server
 * state, "connected" is a per-browser convenience flag.
 */

import { useEffect, useState } from "react";

const dismissKey = (wsId: string) => `ship.connect-agent.dismissed.${wsId}`;

function claudeCodeCommand(endpoint: string, secret: string | null): string {
  const token = secret ?? "<your-token>";
  return `claude mcp add ship ${endpoint} -t http -H "Authorization: Bearer ${token}"`;
}

export function ConnectAgentCard({
  workspaceId,
  mcpEndpoint,
}: {
  workspaceId: string;
  mcpEndpoint: string;
}) {
  // Start expanded on the server render; flip after mount so the
  // localStorage read never causes a hydration mismatch.
  const [dismissed, setDismissed] = useState<boolean | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [minting, setMinting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    try {
      setDismissed(localStorage.getItem(dismissKey(workspaceId)) === "1");
    } catch {
      setDismissed(false);
    }
  }, [workspaceId]);

  const dismiss = () => {
    try {
      localStorage.setItem(dismissKey(workspaceId), "1");
    } catch {
      /* private mode — collapse for this view only */
    }
    setDismissed(true);
  };
  const reopen = () => {
    try {
      localStorage.removeItem(dismissKey(workspaceId));
    } catch {
      /* ignore */
    }
    setDismissed(false);
  };

  const mint = async () => {
    setMinting(true);
    setError(null);
    try {
      const body = new FormData();
      body.set("name", "operator-agent");
      body.set("ws", workspaceId);
      const res = await fetch("/api/tokens/mint", {
        method: "POST",
        headers: { Accept: "application/json" },
        body,
      });
      if (!res.ok) {
        throw new Error(`Mint failed (${res.status}).`);
      }
      const data = (await res.json()) as { secret?: string };
      if (!data.secret) throw new Error("Mint returned no secret.");
      setSecret(data.secret);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not mint a token.",
      );
    } finally {
      setMinting(false);
    }
  };

  const copy = async (label: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => setCopied((c) => (c === label ? null : c)), 2000);
    } catch {
      setError("Clipboard blocked — copy manually.");
    }
  };

  if (dismissed) {
    return (
      <button
        type="button"
        onClick={reopen}
        data-testid="connect-agent-hint"
        className="block w-full rounded-2xl border border-white/10 bg-white/[0.02] px-5 py-3 text-left text-xs text-white/55 transition hover:border-aqua/30 hover:text-white/80"
      >
        Agent connected ✓ — show setup again
      </button>
    );
  }

  const command = claudeCodeCommand(mcpEndpoint, secret);

  return (
    <section
      data-testid="connect-agent-card"
      className="rounded-2xl border border-aqua/30 bg-aqua/[0.05] p-5"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-[11px] font-bold uppercase tracking-widest text-aqua/90">
            Connect your agent
          </h2>
          <p className="mt-1 max-w-xl text-sm text-white/70">
            Ship is operated from the agent you already live in — attach it
            over MCP and run planning, tickets, approvals, and reviews from
            there. The console stays for settings and destructive confirms.
          </p>
        </div>
        {dismissed === false && (
          <button
            type="button"
            onClick={dismiss}
            data-testid="connect-agent-dismiss"
            className="shrink-0 text-[11px] font-semibold text-white/40 hover:text-white/80"
          >
            Connected ✓ hide
          </button>
        )}
      </div>

      <div className="mt-4 space-y-3">
        <Row label="MCP endpoint">
          <code className="break-all font-mono text-xs text-white/85">
            {mcpEndpoint}
          </code>
        </Row>

        <Row label="Agent token">
          {secret ? (
            <div>
              <code
                data-testid="connect-agent-secret"
                className="block break-all rounded-lg border border-aqua/30 bg-ink/60 p-2 font-mono text-xs text-aqua/95"
              >
                {secret}
              </code>
              <p className="mt-1 text-[10px] text-white/50">
                Copy it now — the secret is never shown again. Revoke any
                time in Settings → Agents &amp; access.
              </p>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={mint}
                disabled={minting}
                data-testid="connect-agent-mint"
                className="rounded-full bg-aqua/80 px-3 py-1.5 text-xs font-bold text-ink transition hover:bg-aqua disabled:opacity-50"
              >
                {minting ? "Minting…" : "Mint agent token"}
              </button>
              <span className="text-[10px] text-white/45">
                or use an existing key from Settings → Agents &amp; access
              </span>
            </div>
          )}
        </Row>

        <Row label="Claude Code">
          <div className="flex items-start gap-2">
            <code className="min-w-0 flex-1 break-all rounded-lg border border-white/10 bg-ink/60 p-2 font-mono text-[11px] text-white/85">
              {command}
            </code>
            <button
              type="button"
              onClick={() => copy("code", command)}
              className="shrink-0 rounded-full border border-white/15 px-2.5 py-1 text-[10px] font-bold text-white/70 hover:text-white"
            >
              {copied === "code" ? "Copied ✓" : "Copy"}
            </button>
          </div>
        </Row>

        <Row label="Claude Desktop">
          <p className="text-xs text-white/65">
            Settings → Connectors → Add custom connector — URL{" "}
            <code className="font-mono text-white/85">{mcpEndpoint}</code>, header{" "}
            <code className="font-mono text-white/85">
              Authorization: Bearer {secret ? "<minted token>" : "<your-token>"}
            </code>
            <button
              type="button"
              onClick={() =>
                copy(
                  "desktop",
                  `Authorization: Bearer ${secret ?? "<your-token>"}`,
                )
              }
              className="ml-2 rounded-full border border-white/15 px-2 py-0.5 text-[10px] font-bold text-white/70 hover:text-white"
            >
              {copied === "desktop" ? "Copied ✓" : "Copy header"}
            </button>
          </p>
        </Row>
      </div>

      {error && <p className="mt-3 text-xs text-coral/95">{error}</p>}
    </section>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-1 gap-1 sm:grid-cols-[8.5rem_minmax(0,1fr)] sm:gap-3">
      <div className="pt-0.5 text-[10px] font-bold uppercase tracking-widest text-white/45">
        {label}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
