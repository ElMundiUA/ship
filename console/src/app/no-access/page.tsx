import Link from "next/link";

export default function NoAccessPage() {
  return (
    <div className="relative min-h-screen overflow-hidden bg-ink text-white">
      {/* gradient backdrop matching login page */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(60%_60%_at_20%_15%,rgba(255,107,107,0.18),transparent),radial-gradient(50%_50%_at_85%_25%,rgba(178,118,255,0.18),transparent),radial-gradient(70%_70%_at_60%_90%,rgba(118,255,217,0.15),transparent)]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 opacity-[0.07] [background-image:linear-gradient(rgba(255,255,255,0.6)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.6)_1px,transparent_1px)] [background-size:48px_48px]"
      />

      <header className="border-b border-white/10 bg-ink/80 px-6 py-4 lg:px-8">
        <div className="mx-auto flex max-w-5xl items-center justify-between">
          <Link href="/" className="flex items-center gap-2 font-display text-lg font-bold">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-coral via-lilac to-aqua text-ink">
              S
            </span>
            Ship
          </Link>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-5xl items-center justify-center px-6 py-16 lg:px-8">
        <div className="rounded-2xl border border-white/10 bg-white/[0.04] backdrop-blur-xl shadow-card p-8 max-w-md text-center">
          <div className="mb-6 flex justify-center">
            <div className="rounded-full bg-coral/15 p-3">
              <span className="block h-12 w-12 rounded-full bg-gradient-to-br from-coral to-lilac flex items-center justify-center text-xl font-bold text-ink">
                ✕
              </span>
            </div>
          </div>

          <h1 className="font-display text-2xl font-bold text-white mb-2">
            Access denied
          </h1>

          <p className="text-base text-white/75 mb-6">
            You don't have access to this resource. If you think this is wrong, ask the
            workspace owner to invite you.
          </p>

          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2.5 text-center text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
          >
            Back to home →
          </Link>
        </div>
      </main>
    </div>
  );
}
