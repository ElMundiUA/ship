import type { Metadata } from "next";
import { DM_Sans, Plus_Jakarta_Sans } from "next/font/google";
import { AdoptionWizardProvider } from "@/components/adoption-wizard";
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
  title: "Ship — methodology your agent can run",
  description:
    "Ship is a methodology kit for shipping safer, faster: Plays you assign as Automations, Runs you watch, an Inbox that catches what needs you. Apache-2.0; one CLI; one config.",
  metadataBase: resolveMetadataBase(),
  openGraph: {
    title: "Ship",
    description: "Plays, Automations, Runs, Inbox — the operator loop your agent can run.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${heading.variable} ${sans.variable}`}>
      <body className="min-h-screen font-sans">
        <AdoptionWizardProvider>{children}</AdoptionWizardProvider>
      </body>
    </html>
  );
}
