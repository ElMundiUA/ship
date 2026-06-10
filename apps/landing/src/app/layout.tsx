import type { Metadata, Viewport } from "next";
import { DM_Sans, Plus_Jakarta_Sans } from "next/font/google";
import { StructuredData } from "@/components/structured-data";
import { resolveMetadataBase } from "@/lib/site-url";
import "./globals.css";

/** Headings: readable geometric sans (replaces ultra-wide Syne for hero/body comfort). */
const heading = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-heading",
  display: "swap",
  adjustFontFallback: true,
});

const sans = DM_Sans({
  subsets: ["latin"],
  variable: "--font-dm",
  display: "swap",
  adjustFontFallback: true,
});

/**
 * Sitewide metadata.
 *
 * Title and description live here as fallbacks; per-route ``page.tsx``
 * files override with their own ``Metadata`` blocks. The Open Graph and
 * Twitter images are intentionally NOT set here — Next.js auto-injects
 * the file-based ``app/opengraph-image.tsx`` and ``app/twitter-image.tsx``
 * artefacts as the canonical share images, with a per-route override
 * possible by dropping the same files next to a route's ``page.tsx``.
 */
export const metadata: Metadata = {
  metadataBase: resolveMetadataBase(),
  title: {
    default: "Ship — if you can describe it, you can ship it",
    template: "%s — Ship",
  },
  description:
    "Ship turns your idea into a live app for non-technical founders — describe what you want, Ship builds it, you share a real preview.",
  applicationName: "Ship",
  authors: [{ name: "Denys Kuzin" }],
  keywords: [
    "founder",
    "no-code alternative",
    "idea to app",
    "AI-assisted delivery",
    "product delivery workspace",
    "AI agent orchestration",
    "process automation",
    "specialists",
    "Inbox",
    "workspace audit",
    "Linear",
    "Jira",
    "GitHub Actions",
    "Cursor",
    "Claude Code",
    "Copilot",
    "Codex",
  ],
  openGraph: {
    title: "Ship — if you can describe it, you can ship it",
    description:
      "Describe your idea in plain English. Ship scaffolds, builds, and ships a preview you can share — built for founders.",
    siteName: "Ship",
    type: "website",
    locale: "en_US",
  },
  twitter: {
    card: "summary_large_image",
    title: "Ship — if you can describe it, you can ship it",
    description:
      "Describe your idea in plain English. Ship scaffolds, builds, and ships a preview you can share.",
  },
  alternates: {
    canonical: "/",
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
    },
  },
};

export const viewport: Viewport = {
  themeColor: "#05060d",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" data-scroll-behavior="smooth" className={`${heading.variable} ${sans.variable}`}>
      <body className="min-h-screen font-sans">
        <StructuredData />
        {children}
      </body>
    </html>
  );
}
