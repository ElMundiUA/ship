import { ImageResponse } from "next/og";

/**
 * Sitewide default Open Graph image (Next.js file-based convention).
 *
 * Generated at build time as a 1200×630 PNG. Each route can override by
 * dropping its own ``opengraph-image.tsx`` next to its ``page.tsx``; until
 * that happens, every share preview uses this branded default.
 *
 * Rendering uses ``ImageResponse`` (Edge runtime, very small subset of
 * CSS — no Tailwind classes, only inline styles). Satori (the engine
 * behind ``ImageResponse``) only supports ``display: flex | block | none``;
 * any rounded dot or pill needs an explicit ``display: flex`` and a
 * fixed width/height.
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
            "radial-gradient(ellipse 80% 50% at 50% -10%, rgba(207,169,107,0.18), transparent 55%), #05060d",
          padding: "72px",
          color: "#ffffff",
          fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
        }}
      >
        {/* Top — wordmark */}
        <div style={{ display: "flex", alignItems: "center", fontSize: 36, fontWeight: 700 }}>
          <span style={{ letterSpacing: "-0.02em" }}>Ship</span>
          <span style={{ color: "#cfa96b" }}>.</span>
        </div>

        {/* Middle — headline */}
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          <div
            style={{
              display: "flex",
              fontSize: 18,
              fontWeight: 700,
              letterSpacing: "0.18em",
              color: "rgba(207,169,107,0.85)",
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

        {/* Bottom — strip with three pillars + URL */}
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
              <div
                style={{
                  display: "flex",
                  width: 8,
                  height: 8,
                  borderRadius: 999,
                  background: "#cfa96b",
                }}
              />
              Workspace
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div
                style={{
                  display: "flex",
                  width: 8,
                  height: 8,
                  borderRadius: 999,
                  background: "#FFC857",
                }}
              />
              Inbox
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div
                style={{
                  display: "flex",
                  width: 8,
                  height: 8,
                  borderRadius: 999,
                  background: "#D1A7FF",
                }}
              />
              Audit
            </div>
          </div>
          <div style={{ display: "flex", fontSize: 18, color: "rgba(255,255,255,0.45)" }}>
            ship.elmundi.com
          </div>
        </div>
      </div>
    ),
    {
      ...size,
    },
  );
}
