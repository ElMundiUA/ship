import Link from "next/link";

import { isApiConfigured } from "@/lib/api/client";
import { isAuth0Mode } from "@/lib/auth0";

import { LoginForm } from "./login-form";

// SHIP_API_URL is read from the runtime environment, not the build-time one,
// so the page must opt out of static prerendering or the "live/mock" state
// would freeze at build time (when no backend is configured).
export const dynamic = "force-dynamic";

type SearchParams = Promise<{ [key: string]: string | string[] | undefined }>;

export default async function LoginPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const params = await searchParams;
  const apiConfigured = isApiConfigured();
  const error = typeof params.error === "string" ? params.error : undefined;
  const email = typeof params.email === "string" ? params.email : undefined;
  const reason = typeof params.reason === "string" ? params.reason : undefined;
  const mode =
    params.mode === "signup" || params.mode === "login"
      ? (params.mode as "signup" | "login")
      : undefined;
  const requestedLocalMode = params.mode === "local";
  return (
    <div className="relative min-h-screen overflow-hidden bg-ink text-white">
      {/* gradient backdrop matching marketing chrome */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(60%_60%_at_20%_15%,rgba(255,107,107,0.18),transparent),radial-gradient(50%_50%_at_85%_25%,rgba(178,118,255,0.18),transparent),radial-gradient(70%_70%_at_60%_90%,rgba(118,255,217,0.15),transparent)]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 opacity-[0.07] [background-image:linear-gradient(rgba(255,255,255,0.6)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.6)_1px,transparent_1px)] [background-size:48px_48px]"
      />

      <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-6">
        <Link href="/" className="flex items-center gap-2 font-display text-lg font-bold">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-coral via-lilac to-aqua text-ink">
            S
          </span>
          Ship
        </Link>
        <div className="text-xs text-white/55">
          New to Ship?{" "}
          <Link href="/onboarding" className="font-semibold text-aqua hover:underline">
            Create a workspace →
          </Link>
        </div>
      </header>

      {reason === "session_expired" && (
        <div className="mx-auto w-full max-w-6xl px-6 pb-4">
          <div className="rounded-lg border border-sun/30 bg-sun/[0.08] px-4 py-2.5 text-sm text-sun/95">
            Your session expired — please sign in again.
          </div>
        </div>
      )}

      <main className="mx-auto grid w-full max-w-6xl grid-cols-1 gap-10 px-6 pb-16 pt-6 lg:grid-cols-[1.05fr_0.95fr] lg:items-center">
        <section className="hidden lg:block">
          <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
            Operator console · preview
          </p>
          <h1 className="mt-3 font-display text-5xl font-bold leading-[1.05] tracking-tight">
            Sign in to keep
            <br />
            your delivery cadence
            <br />
            <span className="bg-gradient-to-r from-coral via-lilac to-aqua bg-clip-text text-transparent">
              honest.
            </span>
          </h1>
          <p className="mt-5 max-w-md text-base leading-relaxed text-white/75">
            One place to review yesterday&apos;s shipping, approve retro action items, and
            curate the artifact catalog your agents pull every day.
          </p>

          <ul className="mt-8 space-y-3 text-sm">
            {[
              "Daily digest + retro queue across every project",
              "Workspace catalog overlays the global one",
              "Knowledge buckets your agents can fetch from CLI",
              "Telemetry exported to OTel, S3, or webhook",
            ].map((b) => (
              <li key={b} className="flex items-start gap-2.5 text-white/80">
                <span className="mt-1 inline-block h-1.5 w-1.5 rounded-full bg-aqua" />
                {b}
              </li>
            ))}
          </ul>
        </section>

        <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-7 backdrop-blur-xl shadow-card">
          {isAuth0Mode && !requestedLocalMode ? (
            <Auth0SignInPanel next={typeof params.next === "string" ? params.next : "/"} />
          ) : (
            <LoginForm
              apiConfigured={apiConfigured}
              initialError={error}
              initialEmail={email}
              initialMode={mode}
            />
          )}

          <p className="mt-6 text-[11px] leading-snug text-white/45">
            By signing in you accept the Ship workspace terms. Your tenant data
            never leaves your workspace boundary.
          </p>
        </section>
      </main>
    </div>
  );
}

function Auth0SignInPanel({ next }: { next: string }) {
  const loginHref = `/auth/login?returnTo=${encodeURIComponent(next || "/")}`;
  return (
    <div>
      <div className="mb-5 flex items-center justify-between">
        <h2 className="font-display text-2xl font-bold">Sign in</h2>
        <span className="rounded-full bg-aqua/15 px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest text-aqua">
          auth0
        </span>
      </div>
      <p className="text-sm text-white/70">
        This deployment authenticates via Auth0. You&apos;ll be sent to your
        organization&apos;s identity provider, then bounced back here.
      </p>
      <a
        href={loginHref}
        className="mt-5 block w-full rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2.5 text-center text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
      >
        Continue with Auth0 →
      </a>
      <p className="mt-3 text-center text-[11px] text-white/45">
        First time here? The platform will create your account on the
        first successful login.
      </p>
    </div>
  );
}
