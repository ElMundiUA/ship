import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { BlogMarkdown } from "@/components/blog-content";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { formatBlogDate, getBlogPost, listBlogPosts } from "@/lib/blog";
import { preprocessBlogMarkdown } from "@/lib/blog-markdown";
import { resolveMetadataBase } from "@/lib/site-url";

type Params = { slug: string };

export function generateStaticParams(): Params[] {
  return listBlogPosts().map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { slug } = await params;
  const post = getBlogPost(slug);
  if (!post) return { title: "Post not found — Ship Log" };
  return {
    title: `${post.title} — Ship Log`,
    description: post.description,
    openGraph: {
      title: post.title,
      description: post.description,
      type: "article",
      publishedTime: post.date,
      authors: [post.author],
    },
  };
}

export default async function BlogPostPage({ params }: { params: Promise<Params> }) {
  const { slug } = await params;
  const post = getBlogPost(slug);
  if (!post) notFound();

  const posts = listBlogPosts();
  const idx = posts.findIndex((p) => p.slug === slug);
  const prev = idx >= 0 && idx < posts.length - 1 ? posts[idx + 1] : null;
  const next = idx > 0 ? posts[idx - 1] : null;

  const body = preprocessBlogMarkdown(post.body);

  // BlogPosting JSON-LD — Google indexes this and shows the article in
  // rich-result carousels. Keys map to schema.org BlogPosting fields.
  const siteUrl = resolveMetadataBase().toString().replace(/\/$/, "");
  const blogPosting = {
    "@context": "https://schema.org",
    "@type": "BlogPosting",
    mainEntityOfPage: {
      "@type": "WebPage",
      "@id": `${siteUrl}/blog/${slug}`,
    },
    headline: post.title,
    description: post.description,
    datePublished: post.date,
    dateModified: post.date,
    author: { "@type": "Person", name: post.author },
    publisher: {
      "@type": "Organization",
      name: "Ship",
      logo: { "@type": "ImageObject", url: `${siteUrl}/icon` },
    },
    keywords: post.tags.join(", "),
  };

  return (
    <>
      <SiteHeader />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(blogPosting) }}
      />
      <main className="pt-24 sm:pt-28">
        <article>
          {/* Masthead */}
          <header className="blog-shell pt-6 sm:pt-8">
            <div className="mb-4 flex items-center gap-2 text-xs font-semibold text-white/45">
              <Link href="/blog" className="rounded-md px-1.5 py-0.5 transition hover:bg-white/5 hover:text-white">
                Ship Log
              </Link>
              <span className="text-white/25">/</span>
              <span className="text-white/60">{post.kicker}</span>
            </div>
            <p className="text-[11px] font-bold uppercase tracking-[0.3em] text-sun">{post.kicker}</p>
            <h1 className="font-display mt-4 text-[2.25rem] font-bold leading-[1.08] tracking-tight text-white sm:text-[2.8rem] lg:text-[3.2rem]">
              {post.title}
            </h1>
            {post.description ? (
              <p className="mt-6 text-lg leading-relaxed text-white/70 sm:text-xl">{post.description}</p>
            ) : null}
            <div className="mt-8 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-white/10 pt-5 text-sm text-white/55">
              <span className="font-semibold text-white/85">{post.author}</span>
              <span className="text-white/25">·</span>
              <time dateTime={post.date}>{formatBlogDate(post.date)}</time>
              {post.readingTime ? (
                <>
                  <span className="text-white/25">·</span>
                  <span>{post.readingTime} min read</span>
                </>
              ) : null}
              {post.tags.length ? (
                <>
                  <span className="text-white/25">·</span>
                  <span className="flex flex-wrap gap-1.5">
                    {post.tags.map((t) => (
                      <span
                        key={t}
                        className="rounded-full border border-white/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white/60"
                      >
                        {t}
                      </span>
                    ))}
                  </span>
                </>
              ) : null}
            </div>
          </header>

          {/* Body */}
          <div className="blog-shell py-10 sm:py-14">
            <div className="blog-prose prose prose-invert prose-lg max-w-none prose-p:text-white/80 prose-p:leading-relaxed prose-strong:text-white prose-hr:hidden">
              <BlogMarkdown content={body} />
            </div>
          </div>
        </article>

        {/* Prev / Next */}
        <nav className="blog-shell pb-24 sm:pb-32">
          <div className="grid gap-4 border-t border-white/10 pt-10 sm:grid-cols-2">
            {prev ? (
              <Link
                href={`/blog/${prev.slug}`}
                className="group rounded-2xl border border-white/10 bg-white/[0.02] p-5 transition hover:border-white/25 hover:bg-white/[0.04]"
              >
                <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">← Older</p>
                <p className="mt-2 font-display text-base font-bold leading-snug text-white transition group-hover:text-aqua sm:text-lg">
                  {prev.title}
                </p>
              </Link>
            ) : (
              <div />
            )}
            {next ? (
              <Link
                href={`/blog/${next.slug}`}
                className="group rounded-2xl border border-white/10 bg-white/[0.02] p-5 text-right transition hover:border-white/25 hover:bg-white/[0.04]"
              >
                <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">Newer →</p>
                <p className="mt-2 font-display text-base font-bold leading-snug text-white transition group-hover:text-aqua sm:text-lg">
                  {next.title}
                </p>
              </Link>
            ) : (
              <div />
            )}
          </div>
          <div className="mt-10 text-center">
            <Link
              href="/blog"
              className="inline-flex items-center gap-2 text-sm font-semibold text-aqua underline-offset-4 hover:underline"
            >
              <span aria-hidden>←</span> All posts
            </Link>
          </div>
        </nav>
      </main>
      <SiteFooter />
    </>
  );
}
