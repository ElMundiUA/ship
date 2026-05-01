import { ImageResponse } from "next/og";

/**
 * App icon (favicon) — Next.js renders this through ``ImageResponse`` and
 * serves it at ``/icon`` with the right ``rel="icon"`` metadata wired in
 * automatically.
 *
 * Composition: dark rounded square with white "S" mark and an aqua dot
 * stamped bottom-right — same wordmark idea as the navbar logo "Ship.".
 * Kept readable down to 16×16 by avoiding fine details.
 */

export const runtime = "edge";
export const size = { width: 32, height: 32 };
export const contentType = "image/png";

export default function Icon() {
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
          borderRadius: 6,
          color: "#ffffff",
          fontSize: 22,
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
            right: 5,
            bottom: 6,
            width: 6,
            height: 6,
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
