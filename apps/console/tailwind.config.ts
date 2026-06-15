import type { Config } from "tailwindcss";
import tailwindcssAnimate from "tailwindcss-animate";

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
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      colors: {
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        border: "hsl(var(--border) / 0.1)",
        input: "hsl(var(--input) / 0.15)",
        ring: "hsl(var(--ring))",
        ink: "#0b1020",
        mist: "#e8f4ff",
        coral: "#ff5c6c",
        // `aqua` keeps the brand-key name for backwards compatibility but
        // now renders as a warm muted champagne gold — same calibration as
        // the landing site (sprint A of the console redesign). Replaces
        // the earlier neon teal #2ee6d6 which read as cheap on big surfaces.
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
    },
  },
  plugins: [tailwindcssAnimate],
};

export default config;
