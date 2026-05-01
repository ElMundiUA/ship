import { ImageResponse } from "next/og";

/**
 * Apple touch icon — 180×180 PNG used by iOS / iPadOS when a visitor
 * "Add to Home Screen"s the site. Same mark as the favicon, scaled up so
 * the dot reads cleanly at homescreen size.
 */

export const runtime = "edge";
export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#05060d",
          color: "#ffffff",
          fontSize: 124,
          fontWeight: 800,
          fontFamily: "system-ui, -apple-system, sans-serif",
          letterSpacing: "-0.04em",
          position: "relative",
        }}
      >
        S
        <div
          style={{
            position: "absolute",
            right: 32,
            bottom: 36,
            width: 28,
            height: 28,
            borderRadius: 999,
            background: "#2EE6D6",
          }}
        />
      </div>
    ),
    {
      ...size,
    },
  );
}
