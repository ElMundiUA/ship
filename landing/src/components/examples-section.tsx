import Image from "next/image";
import Link from "next/link";

type Preview =
  | {
      kind: "internal";
      href: string;
      src: string;
      alt: string;
      caption: string;
    }
  | {
      kind: "external";
      href: string;
      src: string;
      alt: string;
      caption: string;
    };

const previewShots: Preview[] = [
  {
    kind: "internal",
    href: "/use-cases",
    src: "/use-cases/use-cases-index.png",
    alt: "Ship — use cases hub with dark theme and case cards",
    caption: "Use cases hub",
  },
  {
    kind: "internal",
    href: "/use-cases/ship",
    src: "/use-cases/ship-home.png",
    alt: "Ship methodology — marketing home",
    caption: "Ship kit story",
  },
  {
    kind: "external",
    href: "https://dev.elmundi.com/",
    src: "/use-cases/elmundi-dev-home.png",
    alt: "ElMundi — consumer history app (staging)",
    caption: "ElMundi product · staging",
  },
];

function PreviewCard({ p }: { p: Preview }) {
  const inner = (
    <>
      <Image
        src={p.src}
        alt={p.alt}
        fill
        sizes="(max-width: 640px) 100vw, 33vw"
        className="object-cover object-top transition duration-300 group-hover:scale-[1.03]"
        unoptimized
      />
      <span className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 to-transparent px-3 py-3 text-xs font-semibold text-white/95">
        {p.caption}
        {p.kind === "external" ? " · opens in new tab" : " →"}
      </span>
    </>
  );

  const className =
    "group relative block aspect-[16/10] overflow-hidden rounded-2xl border border-white/10 bg-black/40 shadow-card transition hover:border-aqua/40";

  if (p.kind === "external") {
    return (
      <a href={p.href} target="_blank" rel="noopener noreferrer" className={className}>
        {inner}
      </a>
    );
  }

  return (
    <Link href={p.href} className={className}>
      {inner}
    </Link>
  );
}

export function ExamplesSection() {
  return (
    <section id="use-cases" className="border-y border-white/10 bg-black/25 py-20 sm:py-24">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-sm font-bold uppercase tracking-widest text-coral">Use cases</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">See the framework in real pages</h2>
            <p className="mt-3 max-w-2xl text-white/65">
              Stories match how enterprise teams evaluate vendors — challenge, solution, outcomes, evidence. Previews use
              this site&apos;s UI plus the ElMundi consumer experience on staging (not the Ship manual reader).
            </p>
          </div>
          <div className="flex shrink-0 flex-col gap-2 sm:flex-row sm:items-center">
            <Link href="/use-cases" className="btn-primary inline-flex text-center">
              Open use cases
            </Link>
            <Link href="/use-cases/elmundi" className="btn-secondary inline-flex text-center">
              ElMundi reference
            </Link>
            <Link href="/docs/examples/elmundi" className="btn-secondary inline-flex text-center">
              Deep-dive manual
            </Link>
          </div>
        </div>

        <div className="mt-10 grid gap-4 sm:grid-cols-3">
          {previewShots.map((p) => (
            <PreviewCard key={p.href + p.src} p={p} />
          ))}
        </div>
      </div>
    </section>
  );
}
