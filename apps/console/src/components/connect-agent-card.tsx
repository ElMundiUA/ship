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
 *
 * Pilot surface for ELS-309 shadcn migration — uses @/components/ui/*
 * primitives; legacy ui.tsx and globals.css button utilities unchanged
 * elsewhere.
 */

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

const dismissKey = (wsId: string) => `ship.connect-agent.dismissed.${wsId}`;

/** OAuth path (recommended): no token in the command — the agent
 * discovers Ship's authorization server, opens a login, and you approve
 * a workspace in the browser. */
function oauthCommand(endpoint: string): string {
  return `claude mcp add ship ${endpoint} -t http`;
}

/** Token fallback (CI / headless): bearer a minted PAT directly. */
function tokenCommand(endpoint: string, secret: string | null): string {
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
      <Button
        type="button"
        variant="ghost"
        onClick={reopen}
        data-testid="connect-agent-hint"
        className="h-auto w-full justify-start rounded-2xl border border-white/10 bg-white/[0.02] px-5 py-3 text-left text-xs font-normal text-white/55 hover:border-aqua/30 hover:text-white/80"
      >
        Agent connected ✓ — show setup again
      </Button>
    );
  }

  const oauthCmd = oauthCommand(mcpEndpoint);
  const tokenCmd = tokenCommand(mcpEndpoint, secret);

  return (
    <Card
      data-testid="connect-agent-card"
      className="relative overflow-hidden rounded-xl border-aqua/25 bg-card shadow-[0_10px_30px_-16px_rgba(0,0,0,0.55)] ring-1 ring-inset ring-white/[0.05] [background-image:radial-gradient(120%_140%_at_0%_-10%,rgba(207,169,107,0.10),transparent_55%)]"
    >
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 p-5 pb-0">
        <div>
          <p className="font-mono text-[10px] font-bold uppercase tracking-[0.3em] text-aqua/80">
            MCP edge
          </p>
          <h2 className="mt-1.5 font-display text-xl font-bold tracking-tight text-white">
            Connect your agent
          </h2>
          <p className="mt-2 max-w-xl text-sm text-white/70">
            Ship is operated from the agent you already live in — attach it
            over MCP and run planning, tickets, approvals, and reviews from
            there. The console stays for settings and destructive confirms.
          </p>
        </div>
        {dismissed === false && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={dismiss}
            data-testid="connect-agent-dismiss"
            className="shrink-0 border-0 text-[11px] font-semibold text-white/40 hover:text-white/80"
          >
            Connected ✓ hide
          </Button>
        )}
      </CardHeader>

      <CardContent className="space-y-3 p-5 pt-4">
        <Row label="MCP endpoint">
          <code className="break-all font-mono text-xs text-white/85">
            {mcpEndpoint}
          </code>
        </Row>

        <Separator className="bg-white/10" />

        {/* Primary: OAuth — no token to paste. The agent opens a Ship
            login and you approve a workspace in the browser. */}
        <Row label="Claude Code">
          <div className="flex items-start gap-2">
            <code className="min-w-0 flex-1 break-all rounded-lg border border-white/10 bg-ink/60 p-2 font-mono text-[11px] text-white/85">
              {oauthCmd}
            </code>
            <Button
              type="button"
              variant="secondary"
              size="xs"
              onClick={() => copy("oauth", oauthCmd)}
            >
              {copied === "oauth" ? "Copied ✓" : "Copy"}
            </Button>
          </div>
          <p className="mt-1 text-[10px] text-white/50">
            On first use your agent opens a Ship login and asks you to approve
            a workspace — no token to paste. Revoke any time in Settings →
            Agents &amp; access.
          </p>
        </Row>

        <Row label="Claude Desktop">
          <p className="text-xs text-white/65">
            Settings → Connectors → Add custom connector — URL{" "}
            <code className="font-mono text-white/85">{mcpEndpoint}</code>. It
            walks you through the same login + approve.
          </p>
        </Row>

        {/* Fallback: a long-lived PAT for CI / headless agents that
            can't do an interactive browser login. */}
        <details className="rounded-lg border border-white/10 bg-white/[0.02] p-2.5">
          <summary className="cursor-pointer text-[10px] font-bold uppercase tracking-widest text-white/55 hover:text-white">
            Headless / CI? Use a token instead
          </summary>
          <div className="mt-3 space-y-2">
            {secret ? (
              <code
                data-testid="connect-agent-secret"
                className="block break-all rounded-lg border border-aqua/30 bg-ink/60 p-2 font-mono text-xs text-aqua/95"
              >
                {secret}
              </code>
            ) : (
              <Button
                type="button"
                onClick={mint}
                disabled={minting}
                data-testid="connect-agent-mint"
                size="sm"
                className="bg-aqua/80 text-ink hover:bg-aqua"
              >
                {minting ? "Minting…" : "Mint agent token"}
              </Button>
            )}
            <div className="flex items-start gap-2">
              <code className="min-w-0 flex-1 break-all rounded-lg border border-white/10 bg-ink/60 p-2 font-mono text-[11px] text-white/85">
                {tokenCmd}
              </code>
              <Button
                type="button"
                variant="secondary"
                size="xs"
                onClick={() => copy("token", tokenCmd)}
              >
                {copied === "token" ? "Copied ✓" : "Copy"}
              </Button>
            </div>
            {secret && (
              <p className="text-[10px] text-white/50">
                Copy it now — the secret is never shown again.
              </p>
            )}
          </div>
        </details>
      </CardContent>

      {error && <p className="px-5 pb-5 text-xs text-coral/95">{error}</p>}
    </Card>
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
