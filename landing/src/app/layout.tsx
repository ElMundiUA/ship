import type { Metadata } from "next";
import { DM_Sans, Plus_Jakarta_Sans } from "next/font/google";
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

export const metadata: Metadata = {
  title: "Ship — founder workspace for AI-assisted delivery",
  description:
    "Ship connects repos, trackers, policies, knowledge, automations, and evidence so solo founders and product owners can steer AI-assisted delivery without losing ownership.",
  metadataBase: resolveMetadataBase(),
  openGraph: {
    title: "Ship — founder workspace for AI-assisted delivery",
    description:
      "Ship connects repos, trackers, policies, knowledge, automations, and evidence so solo founders and product owners can steer AI-assisted delivery without losing ownership.",
    url: "https://ship.elmundi.com",
    siteName: "Ship",
    type: "website",
    images: [
      {
        url: "/og-default.png",
        width: 1200,
        height: 630,
        alt: "Ship — product delivery workspace for AI-assisted engineering",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Ship — founder workspace for AI-assisted delivery",
    description:
      "Ship connects repos, trackers, policies, knowledge, automations, and evidence so solo founders and product owners can steer AI-assisted delivery without losing ownership.",
    images: ["/og-default.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" data-scroll-behavior="smooth" className={`${heading.variable} ${sans.variable}`}>
      <body className="min-h-screen font-sans">{children}</body>
    </html>
  );
}
