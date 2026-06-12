"use client";

import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface FormState {
  email: string;
  role: string;
  tracker: string;
  agent: string;
  note: string;
}

interface SubmitState {
  status: "idle" | "submitting" | "success" | "error";
  errorMessage?: string;
}

type WaitlistFormProps = {
  variant?: "full" | "email-only";
};

const ROLE_OPTIONS = [
  { value: "", label: "Select a role..." },
  { value: "Product Owner", label: "Product Owner" },
  { value: "Engineering Manager", label: "Engineering Manager" },
  { value: "Tech Lead", label: "Tech Lead" },
  { value: "Investor", label: "Investor" },
  { value: "Other", label: "Other" },
];

const TRACKER_OPTIONS = [
  { value: "", label: "Select a tracker..." },
  { value: "Linear", label: "Linear" },
  { value: "GitHub Issues", label: "GitHub Issues" },
  { value: "Jira", label: "Jira" },
  { value: "Notion", label: "Notion" },
  { value: "None/Other", label: "None/Other" },
];

const AGENT_OPTIONS = [
  { value: "", label: "Select an agent..." },
  { value: "Cursor", label: "Cursor" },
  { value: "Claude Code", label: "Claude Code" },
  { value: "Codex", label: "Codex" },
  { value: "Copilot", label: "Copilot" },
  { value: "Other", label: "Other" },
  { value: "None", label: "None" },
];

export function WaitlistForm({ variant = "full" }: WaitlistFormProps) {
  const [form, setForm] = useState<FormState>({
    email: "",
    role: "",
    tracker: "",
    agent: "",
    note: "",
  });

  const [submit, setSubmit] = useState<SubmitState>({ status: "idle" });

  const handleInputChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>,
  ) => {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();

    if (!form.email.trim()) {
      setSubmit({
        status: "error",
        errorMessage: "Email is required",
      });
      return;
    }

    setSubmit({ status: "submitting" });

    try {
      const response = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: form.email.trim(),
          role: form.role || null,
          tracker: form.tracker || null,
          agent: form.agent || null,
          note: form.note.trim() || null,
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || "Failed to submit form");
      }

      setSubmit({ status: "success" });
      setForm({
        email: "",
        role: "",
        tracker: "",
        agent: "",
        note: "",
      });
    } catch (error) {
      setSubmit({
        status: "error",
        errorMessage:
          error instanceof Error ? error.message : "An error occurred",
      });
    }
  };

  const emailOnly = variant === "email-only";

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 sm:p-8">
      {submit.status === "success" ? (
        <div className="space-y-4">
          <p className="text-lg font-semibold text-aqua">Thanks — you&apos;re on the list.</p>
          <p className="text-sm leading-relaxed text-white/60">
            {emailOnly
              ? "We'll email you when the next cohort opens. Want to tell us more now?"
              : "We'll evaluate your interest and get back to you with next steps for closed-beta access."}
          </p>
          {emailOnly ? (
            <Link href="/beta" className="text-sm font-semibold text-sun hover:text-white">
              Share more on the beta form →
            </Link>
          ) : null}
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-6">
          <div className="space-y-2">
            <Label htmlFor="email">
              Email <span className="text-red-500">*</span>
            </Label>
            <Input
              id="email"
              name="email"
              type="email"
              required
              value={form.email}
              onChange={handleInputChange}
              placeholder="you@example.com"
              autoComplete="email"
            />
          </div>

          {!emailOnly ? (
            <>
              <div>
                <label
                  htmlFor="role"
                  className="block text-sm font-semibold text-white"
                >
                  Your role
                </label>
                <select
                  id="role"
                  name="role"
                  value={form.role}
                  onChange={handleInputChange}
                  className="mt-2 w-full rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-white focus:border-aqua/50 focus:outline-none"
                >
                  {ROLE_OPTIONS.map((opt) => (
                    <option
                      key={opt.value}
                      value={opt.value}
                      className="bg-slate-900 text-white"
                    >
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label
                  htmlFor="tracker"
                  className="block text-sm font-semibold text-white"
                >
                  Current tracker
                </label>
                <select
                  id="tracker"
                  name="tracker"
                  value={form.tracker}
                  onChange={handleInputChange}
                  className="mt-2 w-full rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-white focus:border-aqua/50 focus:outline-none"
                >
                  {TRACKER_OPTIONS.map((opt) => (
                    <option
                      key={opt.value}
                      value={opt.value}
                      className="bg-slate-900 text-white"
                    >
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label
                  htmlFor="agent"
                  className="block text-sm font-semibold text-white"
                >
                  Current code agent
                </label>
                <select
                  id="agent"
                  name="agent"
                  value={form.agent}
                  onChange={handleInputChange}
                  className="mt-2 w-full rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-white focus:border-aqua/50 focus:outline-none"
                >
                  {AGENT_OPTIONS.map((opt) => (
                    <option
                      key={opt.value}
                      value={opt.value}
                      className="bg-slate-900 text-white"
                    >
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label
                  htmlFor="note"
                  className="block text-sm font-semibold text-white"
                >
                  What would Ship help with?
                </label>
                <textarea
                  id="note"
                  name="note"
                  value={form.note}
                  onChange={handleInputChange}
                  maxLength={500}
                  rows={4}
                  className="mt-2 w-full resize-none rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-white placeholder-white/40 focus:border-aqua/50 focus:outline-none"
                  placeholder="Tell us what problem you're trying to solve..."
                />
                <p className="mt-1 text-xs text-white/50">
                  {form.note.length}/500 characters
                </p>
              </div>
            </>
          ) : null}

          {submit.status === "error" ? (
            <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-3">
              <p className="text-sm text-red-400">{submit.errorMessage}</p>
            </div>
          ) : null}

          <Button type="submit" disabled={submit.status === "submitting"} className="w-full">
            {submit.status === "submitting"
              ? "Submitting..."
              : emailOnly
                ? "Join the waitlist"
                : "Request access"}
          </Button>
        </form>
      )}
    </div>
  );
}
