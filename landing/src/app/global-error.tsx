"use client";

/**
 * Catches failures in the root `layout` (normal `error.tsx` does not).
 * If you see this page, read the message — it is almost always env or hosting config.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: "system-ui,sans-serif", background: "#0b1020", color: "#e8f4ff" }}>
        <div style={{ maxWidth: 560, margin: "4rem auto", padding: "0 1.5rem" }}>
          <h1 style={{ fontSize: "1.25rem", marginBottom: "1rem" }}>Ship landing — something crashed</h1>
          <p style={{ opacity: 0.85, lineHeight: 1.5 }}>
            Check server logs. Common causes: run the app from the <code>landing/</code> app (or set Vercel{" "}
            <strong>Root Directory</strong> to <code>landing</code>), invalid <code>NEXT_PUBLIC_*</code> env, or a
            stale <code>.next</code> — try <code>rm -rf landing/.next</code> then rebuild.
          </p>
          <pre
            style={{
              marginTop: "1.25rem",
              padding: "1rem",
              borderRadius: 8,
              background: "#151b2e",
              overflow: "auto",
              fontSize: "0.85rem",
            }}
          >
            {String(error?.message ?? error)}
            {error?.digest ? `\n\ndigest: ${error.digest}` : ""}
          </pre>
          <button
            type="button"
            onClick={() => reset()}
            style={{
              marginTop: "1.5rem",
              padding: "0.6rem 1.2rem",
              borderRadius: 999,
              border: "none",
              cursor: "pointer",
              fontWeight: 600,
              background: "linear-gradient(90deg,#ff5c6c,#b388ff,#cfa96b)",
              color: "#0b1020",
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
