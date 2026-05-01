import { ImageResponse } from "next/og";

/**
 * Twitter card image — same composition as the Open Graph image.
 *
 * Re-exporting from ``opengraph-image.tsx`` does not work with Next.js's
 * file-based image convention (the build can't see the ``runtime`` and
 * ``size`` fields through a re-export), so the file is duplicated. If
 * one image diverges from the other later, this is the override point.
 */

export const runtime = "edge";
export const alt = "Ship — workspace for AI-assisted product delivery";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default async function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background:
            "radial-gradient(ellipse 80% 50% at 50% -10%, rgba(46,230,214,0.18), transparent 55%), #05060d",
          padding: "72px",
          color: "#ffffff",
          fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", fontSize: 36, fontWeight: 700 }}>
          <span style={{ letterSpacing: "-0.02em" }}>Ship</span>
          <span style={{ color: "#2EE6D6" }}>.</span>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          <div
            style={{
              display: "flex",
              fontSize: 18,
              fontWeight: 700,
              letterSpacing: "0.18em",
              color: "rgba(46,230,214,0.85)",
              textTransform: "uppercase",
            }}
          >
            Process · Specialists · Evidence
          </div>
          <div
            style={{
              display: "flex",
              fontSize: 78,
              fontWeight: 700,
              lineHeight: 1.04,
              letterSpacing: "-0.02em",
              maxWidth: 980,
            }}
          >
            A workspace for AI-assisted product delivery.
          </div>
          <div
            style={{
              display: "flex",
              fontSize: 26,
              color: "rgba(255,255,255,0.65)",
              lineHeight: 1.32,
              maxWidth: 880,
            }}
          >
            Humans own intent. Machines act inside fences. Every action leaves a trail you can read without forensics.
          </div>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "flex-end",
            justifyContent: "space-between",
            borderTop: "1px solid rgba(255,255,255,0.10)",
            paddingTop: 24,
          }}
        >
          <div style={{ display: "flex", gap: 28, fontSize: 18, color: "rgba(255,255,255,0.55)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ display: "flex", width: 8, height: 8, borderRadius: 999, background: "#2EE6D6" }} />
              Workspace
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ display: "flex", width: 8, height: 8, borderRadius: 999, background: "#FFC857" }} />
              Inbox
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ display: "flex", width: 8, height: 8, borderRadius: 999, background: "#D1A7FF" }} />
              Audit
            </div>
          </div>
          <div style={{ display: "flex", fontSize: 18, color: "rgba(255,255,255,0.45)" }}>ship.elmundi.com</div>
        </div>
      </div>
    ),
    {
      ...size,
    },
  );
}
