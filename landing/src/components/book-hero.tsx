import fs from "node:fs";
import path from "node:path";
import Image from "next/image";
import Link from "next/link";

function getBookPdfMeta(): { sizeKb: number; mtime: string } | null {
  try {
    const pdfPath = path.join(process.cwd(), "public", "book.pdf");
    const stat = fs.statSync(pdfPath);
    return {
      sizeKb: Math.round(stat.size / 1024),
      mtime: stat.mtime.toISOString().slice(0, 10),
    };
  } catch {
    return null;
  }
}

export function BookHero() {
  const pdfMeta = getBookPdfMeta();
  return (
    <section className="relative overflow-hidden border-b border-white/10 pb-14 pt-24 sm:pb-16 sm:pt-28">
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.28]"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.06'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E\")",
        }}
      />
      <div className="pointer-events-none absolute -right-24 top-0 h-72 w-72 rounded-full bg-gradient-to-br from-coral/25 via-transparent to-transparent blur-3xl" />
      <div className="pointer-events-none absolute -left-20 bottom-0 h-64 w-64 rounded-full bg-gradient-to-tr from-aqua/20 via-transparent to-transparent blur-3xl" />

      <div className="relative mx-auto max-w-5xl px-4 sm:px-6">
        <div className="grid items-start gap-10 lg:grid-cols-[minmax(200px,280px)_1fr] lg:gap-12">
          <div className="mx-auto w-full max-w-[260px] lg:mx-0 lg:max-w-none">
            <div className="relative aspect-[2/3] overflow-hidden rounded-2xl border border-white/20 bg-black/40 shadow-[0_24px_80px_-12px_rgba(46,230,214,0.25),0_12px_40px_-8px_rgba(255,92,108,0.2)]">
              <Image
                src="/book/cover.png"
                alt="Ship — the book cover: why fences, throughput, and legibility matter"
                width={520}
                height={780}
                className="h-full w-full object-cover"
                sizes="(max-width: 1024px) 260px, 280px"
                priority
              />
            </div>
            <p className="mt-3 text-center text-xs text-white/45 lg:text-left">Cover for the in-browser edition</p>
          </div>

          <div>
            <p className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/[0.06] px-4 py-1.5 text-xs font-semibold uppercase tracking-widest text-sun">
              Long read · why the workspace exists
            </p>
            <h1 className="font-display max-w-3xl text-4xl font-bold leading-tight text-white sm:text-5xl lg:text-[3.1rem] lg:leading-[1.08]">
              The book —{" "}
              <span className="bg-gradient-to-r from-aqua via-lilac to-coral bg-clip-text text-transparent">why Ship</span>
            </h1>
            <p className="mt-5 max-w-2xl text-lg leading-relaxed text-white/70 sm:text-xl">
              The product starts with workspaces, owners, knowledge, and evidence. The book explains why those fences
              exist: quiet systems, human-owned intent, bounded automation, and vendors that stay replaceable.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link href="/" className="btn-secondary">
                ← Landing
              </Link>
              <Link href="/docs" className="btn-secondary">
                Product docs
              </Link>
              <a className="btn-primary" href="#the-idea">
                Start at “The idea”
              </a>
              {pdfMeta ? (
                <a
                  className="inline-flex items-center gap-2 rounded-full border border-aqua/40 bg-aqua/10 px-5 py-2.5 text-sm font-semibold text-aqua transition hover:bg-aqua/20 hover:text-white"
                  href="/book.pdf"
                  download="ship-why-book.pdf"
                >
                  <svg
                    aria-hidden
                    viewBox="0 0 16 16"
                    width="14"
                    height="14"
                    className="opacity-90"
                  >
                    <path
                      d="M8 2v8m0 0l3-3m-3 3L5 7M3 13h10"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                  Download PDF
                  <span className="text-[11px] font-normal text-white/55">
                    {`${(pdfMeta.sizeKb / 1024).toFixed(1)} MB · ${pdfMeta.mtime}`}
                  </span>
                </a>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
