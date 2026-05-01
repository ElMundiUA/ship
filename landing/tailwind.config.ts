import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";

const config: Config = {
  // Resolve globs from this file's directory, not process.cwd() (fixes empty JIT CSS when
  // Next/PostCSS runs with a repo-root cwd in editors or workspace tooling).
  content: {
    relative: true,
    files: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  },
  theme: {
    extend: {
      colors: {
        ink: "#0b1020",
        mist: "#e8f4ff",
        coral: "#ff5c6c",
        // `aqua` keeps the brand-key name for backwards compatibility but
        // now renders as a warm muted champagne gold. The previous teal
        // (#2ee6d6) read as cheap on big surfaces; this hex is calibrated
        // to stay legible on dark backgrounds without blasting the eye.
        aqua: "#cfa96b",
        lilac: "#b388ff",
        sun: "#ffd54a",
      },
      fontFamily: {
        display: [
          "var(--font-heading)",
          "var(--font-dm)",
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
        sans: ["var(--font-dm)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      boxShadow: {
        glow: "0 0 80px -20px rgba(207, 169, 107, 0.40)",
        card: "0 24px 80px -32px rgba(11, 16, 32, 0.45)",
      },
      backgroundImage: {
        "grid-fade":
          "linear-gradient(to right, rgba(255,255,255,0.06) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,0.06) 1px, transparent 1px)",
      },
    },
  },
  plugins: [typography],
};

export default config;
