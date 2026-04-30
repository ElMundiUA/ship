"use client";

const ERROR_COPY: Record<string, string> = {
  missing: "Enter your email address.",
  "email-taken": "That email is already in use by another account.",
  "invalid-request": "Invalid request. Please check the email and try again.",
  "no-backend": "Backend is not wired up here. Set SHIP_API_URL on the console process.",
  "no-session": "Session expired. Please sign in again.",
  unknown: "Unexpected error. Check the console logs and try again.",
};

export function CompleteProfileForm({
  initialError,
  next,
}: {
  initialError?: string;
  next: string;
}) {
  const errorMessage = initialError ? ERROR_COPY[initialError] ?? initialError : null;

  return (
    <div>
      <p className="text-sm text-white/70">
        Enter your email address so we can complete your account setup.
      </p>

      <form action="/api/auth/complete-profile" method="POST" className="mt-4 space-y-3">
        <Field
          label="Email"
          name="email"
          type="email"
          required
          autoComplete="email"
          placeholder="you@company.com"
        />

        <input type="hidden" name="next" value={next} />

        {errorMessage && (
          <p className="rounded-md border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
            {errorMessage}
          </p>
        )}

        <button
          type="submit"
          className="mt-4 w-full rounded-lg bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110 disabled:opacity-60"
        >
          Complete profile
        </button>
      </form>
    </div>
  );
}

function Field({
  label,
  name,
  type = "text",
  required = false,
  autoComplete,
  placeholder,
  defaultValue = "",
}: {
  label: string;
  name: string;
  type?: string;
  required?: boolean;
  autoComplete?: string;
  placeholder?: string;
  defaultValue?: string;
}) {
  return (
    <div>
      <label htmlFor={name} className="block text-xs font-semibold text-white/70">
        {label}
        {required && <span className="text-coral"> *</span>}
      </label>
      <input
        id={name}
        name={name}
        type={type}
        required={required}
        autoComplete={autoComplete}
        placeholder={placeholder}
        defaultValue={defaultValue}
        className="mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-white placeholder-white/40 transition focus:border-aqua/50 focus:bg-white/8 focus:outline-none focus:ring-1 focus:ring-aqua/20"
      />
    </div>
  );
}
