import { redirect } from "next/navigation";

import { getSessionToken } from "@/lib/api/session";
import { CompleteProfileForm } from "./complete-profile-form";

export const dynamic = "force-dynamic";

type SearchParams = Promise<{ [key: string]: string | string[] | undefined }>;

/**
 * Complete profile page for Auth0 users with missing email claim.
 *
 * Shown when a user logs in via an SSO connection that doesn't provide an
 * email claim (e.g. enterprise SAML). They must type their email and submit
 * the form to proceed.
 */
export default async function CompleteProfilePage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const params = await searchParams;
  const token = await getSessionToken();

  // User must be authenticated to access this page
  if (!token) {
    redirect("/login?next=%2Fcomplete-profile&reason=session_expired");
  }

  const error = typeof params.error === "string" ? params.error : undefined;
  const next = typeof params.next === "string" ? params.next : "/";

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
        <div className="flex items-center gap-2 font-display text-lg font-bold">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-coral via-lilac to-aqua text-ink">
            S
          </span>
          Ship
        </div>
      </header>

      <main className="mx-auto grid w-full max-w-6xl grid-cols-1 gap-10 px-6 pb-16 pt-6 lg:grid-cols-[1.05fr_0.95fr] lg:items-center">
        <section className="hidden lg:block">
          <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
            Complete your profile
          </p>
          <h1 className="mt-3 font-display text-5xl font-bold leading-[1.05] tracking-tight">
            Add your email
            <br />
            to complete
            <br />
            <span className="bg-gradient-to-r from-coral via-lilac to-aqua bg-clip-text text-transparent">
              setup.
            </span>
          </h1>
          <p className="mt-5 max-w-md text-base leading-relaxed text-white/75">
            Your identity provider didn&apos;t include your email address. We need it
            to complete your account so you can access the workspace.
          </p>
        </section>

        <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-7 backdrop-blur-xl shadow-card">
          <div className="mb-5">
            <h2 className="font-display text-2xl font-bold">Complete your profile</h2>
          </div>
          <CompleteProfileForm initialError={error} next={next} />

          <p className="mt-6 text-[11px] leading-snug text-white/45">
            By continuing you accept the Ship workspace terms. Your tenant data
            never leaves your workspace boundary.
          </p>
        </section>
      </main>
    </div>
  );
}
