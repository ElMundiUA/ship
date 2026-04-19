import type { Config } from "tailwindcss";

const config: Config = {
  // Resolve globs from this file's directory, not process.cwd() (fixes empty
  // JIT CSS when Next/PostCSS runs with a repo-root cwd in editors / monorepo
  // tooling — same fix as in landing/).
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
        aqua: "#2ee6d6",
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
        glow: "0 0 80px -20px rgba(46, 230, 214, 0.55)",
        card: "0 24px 80px -32px rgba(11, 16, 32, 0.45)",
      },
    },
  },
  plugins: [],
};

export default config;
