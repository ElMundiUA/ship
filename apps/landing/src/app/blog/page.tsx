import type { Metadata } from "next";
import Link from "next/link";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { formatBlogDate, listBlogPosts } from "@/lib/blog";

export const metadata: Metadata = {
  title: "Ship Log — building Ship in public",
  description:
    "Field notes from the Ship team. We do not teach Ship — we run Ship in public. Commit logs, autopsies, trade-offs.",
};

export default function BlogIndex() {
  const posts = listBlogPosts();

  return (
    <>
      <SiteHeader />
      <main className="pt-28">
        {/* Hero — intentionally quiet. 37signals-flavoured kicker + a single sentence of stance. */}
        <section className="mx-auto max-w-3xl px-4 sm:px-6">
          <p className="text-[11px] font-bold uppercase tracking-[0.3em] text-sun">Ship Log</p>
          <h1 className="font-display mt-4 text-4xl font-bold leading-[1.05] tracking-tight text-white sm:text-5xl lg:text-6xl">
            We don&rsquo;t teach Ship.<br />
            <span className="text-white/60">We run Ship in public.</span>
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-relaxed text-white/65 sm:text-xl">
            Field notes from inside the repo. Real commits, real failures, real deletions. Every post
            earns its keep by pointing at code we merged or tore out. No thought leadership. No vibes.
          </p>
          <div className="mt-8 flex flex-wrap gap-2 text-xs font-semibold uppercase tracking-wide text-white/45">
            <span className="rounded-full border border-white/12 px-3 py-1">Build in public</span>
            <span className="rounded-full border border-white/12 px-3 py-1">Autopsies</span>
            <span className="rounded-full border border-white/12 px-3 py-1">Deletions we&rsquo;re proud of</span>
            <span className="rounded-full border border-white/12 px-3 py-1">Numbers from the log</span>
          </div>
        </section>

        {/* Posts list — stacked, Rework-style. No cards. Typographic rhythm carries it. */}
        <section className="mx-auto mt-20 max-w-3xl px-4 pb-28 sm:px-6 sm:pb-32">
          <div className="flex items-baseline justify-between border-b border-white/10 pb-4">
            <h2 className="font-display text-lg font-bold tracking-tight text-white">All posts</h2>
            <p className="text-xs uppercase tracking-[0.2em] text-white/40">{posts.length} published</p>
          </div>
          {posts.length === 0 ? (
            <p className="mt-8 text-white/55">No posts yet.</p>
          ) : (
            <ol className="divide-y divide-white/10">
              {posts.map((post) => (
                <li key={post.slug} className="py-8 sm:py-10">
                  <Link href={`/blog/${post.slug}`} className="group block">
                    <div className="flex items-center gap-3 text-[11px] font-bold uppercase tracking-[0.22em] text-aqua/85">
                      <span>{post.kicker}</span>
                      <span className="text-white/25">·</span>
                      <time dateTime={post.date} className="text-white/45">
                        {formatBlogDate(post.date)}
                      </time>
                      {post.readingTime ? (
                        <>
                          <span className="text-white/25">·</span>
                          <span className="text-white/45">{post.readingTime} min read</span>
                        </>
                      ) : null}
                    </div>
                    <h3 className="font-display mt-3 text-2xl font-bold leading-tight tracking-tight text-white transition group-hover:text-aqua sm:text-[1.9rem]">
                      {post.title}
                    </h3>
                    <p className="mt-3 max-w-2xl text-base leading-relaxed text-white/65 sm:text-lg">
                      {post.description}
                    </p>
                    <span className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-aqua/85 transition group-hover:text-aqua">
                      Read the post
                      <span aria-hidden>→</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ol>
          )}
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
