"use client";

import { useState } from "react";

type Mode = "login" | "signup";

const ERROR_COPY: Record<string, string> = {
  missing: "Enter your email and password.",
  invalid: "Invalid email or password.",
  exists: "That email is already registered. Try signing in.",
  "weak-password": "Password must be at least 8 characters.",
  "no-backend":
    "Backend is not wired up here. Set SHIP_API_URL on the console process.",
  unavailable: "Backend unreachable. Check that ship-server is running.",
  unknown: "Unexpected error. Check the console logs and try again.",
};

export function LoginForm({
  apiConfigured,
  initialError,
  initialEmail,
  initialMode,
}: {
  apiConfigured: boolean;
  initialError?: string;
  initialEmail?: string;
  initialMode?: Mode;
}) {
  const [mode, setMode] = useState<Mode>(initialMode ?? "login");
  const errorMessage = initialError ? ERROR_COPY[initialError] ?? initialError : null;

  return (
    <div>
      <div className="mb-5 flex items-center justify-between">
        <h2 className="font-display text-2xl font-bold">
          {mode === "login" ? "Sign in" : "Create account"}
        </h2>
        <span
          className={
            "rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest " +
            (apiConfigured
              ? "bg-aqua/15 text-aqua"
              : "bg-sun/20 text-sun")
          }
          title={
            apiConfigured
              ? "SHIP_API_URL is set; submitting hits the real backend."
              : "SHIP_API_URL is not set; the form will tell you so."
          }
        >
          {apiConfigured ? "live" : "mock"}
        </span>
      </div>

      <form
        action={mode === "login" ? "/api/auth/login" : "/api/auth/signup"}
        method="POST"
        className="space-y-3"
      >
        {mode === "signup" && (
          <Field
            label="Display name"
            name="display_name"
            type="text"
            placeholder="Your name (optional)"
            autoComplete="name"
          />
        )}
        <Field
          label="Email"
          name="email"
          type="email"
          required
          autoComplete="email"
          placeholder="you@company.com"
          defaultValue={initialEmail ?? ""}
        />
        <Field
          label="Password"
          name="password"
          type="password"
          required
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          placeholder={mode === "signup" ? "At least 8 characters" : "••••••••••••"}
        />

        {errorMessage && (
          <p className="rounded-md border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
            {errorMessage}
          </p>
        )}

        {mode === "login" && (
          <div className="flex items-center justify-between text-[11px]">
            <label className="flex items-center gap-2 text-white/65">
              <input type="checkbox" defaultChecked className="accent-aqua" />
              Remember this device
            </label>
            <a className="font-semibold text-aqua hover:underline" href="#">
              Forgot password?
            </a>
          </div>
        )}

        <button
          type="submit"
          className="mt-1 block w-full rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2.5 text-center text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
        >
          {mode === "login" ? "Sign in" : "Create account"}
        </button>
      </form>

      <div className="my-5 flex items-center gap-3 text-[10px] uppercase tracking-widest text-white/35">
        <span className="h-px flex-1 bg-white/10" />
        or
        <span className="h-px flex-1 bg-white/10" />
      </div>

      <div className="text-center text-xs text-white/65">
        {mode === "login" ? (
          <>
            New to Ship?{" "}
            <button
              type="button"
              onClick={() => setMode("signup")}
              className="font-semibold text-aqua hover:underline"
            >
              Create an account
            </button>
          </>
        ) : (
          <>
            Already have one?{" "}
            <button
              type="button"
              onClick={() => setMode("login")}
              className="font-semibold text-aqua hover:underline"
            >
              Sign in
            </button>
          </>
        )}
      </div>

      <div className="mt-5 grid grid-cols-2 gap-2 opacity-60">
        <ProviderButton label="GitHub" />
        <ProviderButton label="Google" />
        <ProviderButton label="SSO" />
        <ProviderButton label="CLI token" />
      </div>
      <p className="mt-3 text-center text-[10px] text-white/40">
        OAuth & SSO arrive once their app credentials are configured.
      </p>
    </div>
  );
}

function Field({
  label,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[11px] font-bold uppercase tracking-widest text-white/55">
        {label}
      </span>
      <input
        {...props}
        className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3.5 py-2.5 text-sm text-white outline-none focus:border-aqua/40"
        suppressHydrationWarning
      />
    </label>
  );
}

function ProviderButton({ label }: { label: string }) {
  return (
    <button
      type="button"
      disabled
      className="cursor-not-allowed rounded-full border border-white/10 bg-white/[0.04] px-3 py-2 text-xs font-semibold text-white/85"
    >
      {label}
    </button>
  );
}
