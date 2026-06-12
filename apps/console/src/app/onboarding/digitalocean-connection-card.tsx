"use client";

import { useEffect, useState } from "react";

const DO_SIGNUP_URL =
  process.env.NEXT_PUBLIC_DIGITALOCEAN_REFERRAL_URL ||
  "https://cloud.digitalocean.com/registrations/new";

type ProviderState = {
  provider: string;
  label: string;
  connected: boolean;
};

export function DigitalOceanConnectionCard({ wsId }: { wsId: string }) {
  const [providers, setProviders] = useState<ProviderState[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const connected = providers.some(
    (p) => p.provider === "digitalocean" && p.connected,
  );

  useEffect(() => {
    fetch(`/api/deploy/providers?ws=${encodeURIComponent(wsId)}`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d: ProviderState[]) => setProviders(d))
      .catch(() => setProviders([]));
  }, [wsId]);

  const connect = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/deploy/connect", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspaceId: wsId,
          returnPath: `/onboarding?step=tracker&ws=${encodeURIComponent(wsId)}`,
        }),
      });
      if (!res.ok) {
        setError("Could not start DigitalOcean connect.");
        return;
      }
      const data = (await res.json()) as { install_url: string };
      window.location.href = data.install_url;
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-connected={String(connected)}
      className={`flex h-full flex-col rounded-2xl border p-4 backdrop-blur-xl shadow-card transition ${
        connected
          ? "border-aqua/50 bg-aqua/[0.07]"
          : "border-white/[0.08] bg-white/[0.02] hover:border-aqua/40"
      }`}
    >
      <div className="flex items-center justify-between">
        <h3 className="font-display text-lg font-bold text-white">
          DigitalOcean
        </h3>
        <span
          className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-widest ${
            connected
              ? "border-aqua/35 bg-aqua/10 text-aqua"
              : "border-white/[0.08] bg-white/[0.04] text-white/55"
          }`}
        >
          {connected ? "Connected" : "Deploy provider"}
        </span>
      </div>
      <p className="mt-2 flex-1 text-[12px] leading-relaxed text-white/65">
        Deploy apps into your own DigitalOcean account. Ship stores the OAuth
        token encrypted and uses it to create, update, and remove App Platform
        apps.
      </p>

      <details className="mt-4 rounded-xl border border-white/[0.08] bg-black/10 p-3">
        <summary className="cursor-pointer text-xs font-semibold text-white/70">
          No DigitalOcean account?
        </summary>
        <ol className="mt-3 list-decimal space-y-2 pl-4 text-xs leading-relaxed text-white/60">
          <li>
            Create an account from the{" "}
            <a
              href={DO_SIGNUP_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-aqua underline"
            >
              DigitalOcean credit link
            </a>
            .
          </li>
          <li>Add billing in DigitalOcean so App Platform can run.</li>
          <li>Return here and click Connect DigitalOcean.</li>
        </ol>
      </details>

      {error && (
        <p className="mt-3 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
          {error}
        </p>
      )}

      <button
        type="button"
        onClick={connect}
        disabled={busy || connected}
        className="mt-4 inline-flex items-center justify-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2 text-sm font-semibold text-ink shadow-glow transition hover:brightness-110 active:scale-[0.99] disabled:cursor-default disabled:bg-none disabled:bg-white/[0.05] disabled:text-white/45 disabled:shadow-none"
      >
        {connected ? "Connected ✓" : busy ? "Redirecting…" : "Connect DigitalOcean →"}
      </button>
    </div>
  );
}
