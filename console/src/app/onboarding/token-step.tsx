"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import type { ApiTokenMint } from "@/lib/api/types";

const ERROR_COPY: Record<string, string> = {
  api_unavailable: "Backend not reachable. Set SHIP_API_URL and try again.",
  session_expired: "Your session expired — please sign in again.",
  missing_workspace: "We lost the workspace id. Restart the wizard.",
};

export function OnboardingTokenStep({
  wsId,
  seeded,
}: {
  wsId: string;
  seeded?: string;
}) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [token, setToken] = useState<ApiTokenMint | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function mint() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/onboard/mint-token", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspace_id: wsId,
          name: "shipctl on this laptop",
        }),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        const code = (payload as { error?: string }).error ?? `http_${res.status}`;
        if (code === "session_expired") {
          router.push("/login?error=session_expired");
          return;
        }
        setError(ERROR_COPY[code] ?? code);
        return;
      }
      const data = (await res.json()) as ApiTokenMint;
      setToken(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }

  async function copy() {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token.secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignored — user can still select-copy from the input below.
    }
  }

  return (
    <section>
      <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
        Step 6 of 6
      </p>
      <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
        Mint a CLI token.
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        We&apos;ll generate a Personal Access Token scoped to this workspace so{" "}
        <code className="rounded bg-white/5 px-1 py-[1px] text-aqua">shipctl</code> can talk
        to the backend. The plaintext is shown <strong>once</strong> on this page — copy it
        somewhere safe; the server only keeps a SHA-256 hash.
      </p>

      {seeded && Number(seeded) > 0 && (
        <div className="mt-5 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-white/85">
          <strong className="text-aqua">
            Seeded {seeded} knowledge doc{Number(seeded) === 1 ? "" : "s"}.
          </strong>{" "}
          They&apos;re committed under <code className="text-aqua">.ship/knowledge/</code> in
          your repo — review them in your editor.
        </div>
      )}

      {error && (
        <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
          {error}
        </div>
      )}

      {!token ? (
        <div className="mt-7 flex items-center justify-between gap-3">
          <Link
            href={`/onboarding?step=done&ws=${wsId}`}
            className="text-xs text-white/55 hover:text-white"
          >
            Skip for now →
          </Link>
          <button
            type="button"
            onClick={mint}
            disabled={loading}
            className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110 disabled:opacity-60"
          >
            {loading ? "Minting…" : "Generate token →"}
          </button>
        </div>
      ) : (
        <div className="mt-7 space-y-5">
          <div className="rounded-2xl border border-aqua/40 bg-aqua/[0.06] p-5">
            <div className="mb-2 flex items-center justify-between">
              <span className="font-mono text-[11px] uppercase tracking-widest text-aqua/85">
                Personal Access Token · shown once
              </span>
              <button
                type="button"
                onClick={copy}
                className="rounded-full border border-aqua/40 px-3 py-1 text-[11px] font-bold text-aqua hover:bg-aqua/10"
              >
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <input
              readOnly
              value={token.secret}
              className="w-full select-all rounded-lg border border-white/10 bg-ink/60 px-3.5 py-2.5 font-mono text-xs text-white outline-none"
              onFocus={(e) => e.currentTarget.select()}
            />
            <p className="mt-2 text-[11px] text-white/55">
              Scopes: {token.scopes.join(", ") || "(none)"} · Expires:{" "}
              {token.expires_at ? new Date(token.expires_at).toLocaleDateString() : "never"}
            </p>
          </div>

          <div className="rounded-xl border border-white/10 bg-white/[0.025] p-4 font-mono text-[11px] text-white/70">
            <div className="mb-1 text-white/50">Try it:</div>
            <pre className="whitespace-pre-wrap leading-relaxed text-white">{`export SHIP_TOKEN=${token.secret.slice(0, 12)}…
shipctl ls --workspace ${wsId}`}</pre>
          </div>

          <div className="flex items-center justify-end gap-3 pt-2">
            <Link
              href={`/onboarding?step=done&ws=${wsId}`}
              className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
            >
              I saved it · finish →
            </Link>
          </div>
        </div>
      )}
    </section>
  );
}
