import type { Metadata } from "next";
import { DM_Sans, Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

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
  title: "Ship · console",
  description:
    "Operator console for the Ship cloud platform — workspace catalog, knowledge buckets, daily/retro, telemetry.",
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${heading.variable} ${sans.variable}`}>
      <body className="min-h-screen font-sans">{children}</body>
    </html>
  );
}
