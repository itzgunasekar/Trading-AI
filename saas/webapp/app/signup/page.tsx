"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import Link from "next/link";
import { Sparkles, CheckCircle2, AlertCircle } from "lucide-react";

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) return setError("Passwords don't match.");
    if (password.length < 12) return setError("Password must be at least 12 characters.");
    if (!agreed) return setError("You must accept the terms to continue.");
    setSubmitting(true);
    try {
      const res = await fetch("/api/auth/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || "Signup failed");
      }
      setSuccess(true);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (success) {
    return (
      <div className="min-h-[calc(100vh-160px)] grid place-items-center px-6">
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          className="glass-strong rounded-2xl p-10 max-w-md text-center gradient-border"
        >
          <div className="w-16 h-16 rounded-full bg-emerald-500/20 grid place-items-center mx-auto mb-6">
            <CheckCircle2 size={32} className="text-emerald-400" />
          </div>
          <h2 className="text-2xl font-bold mb-3">Application received!</h2>
          <p className="text-slate-300 mb-6">
            We&apos;ll review your application and email <strong>{email}</strong> when you&apos;re approved.
          </p>
          <Link href="/" className="btn-secondary inline-block">Back home</Link>
        </motion.div>
      </div>
    );
  }

  return (
    <div className="min-h-[calc(100vh-160px)] grid place-items-center px-6 py-20">
      <motion.div
        initial={{ opacity: 0, y: 30 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="w-full max-w-md"
      >
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full glass text-xs text-slate-300 mb-4">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot" />
            Beta access
          </div>
          <h1 className="text-3xl font-bold mb-2">Apply for access</h1>
          <p className="text-slate-400 text-sm">
            Manually reviewed within 24 hours.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="glass-strong rounded-2xl p-8 space-y-5 gradient-border">
          <Field label="Email" type="email" value={email} onChange={setEmail} />
          <Field label="Password" type="password" value={password} onChange={setPassword} hint="12+ characters" />
          <Field label="Confirm password" type="password" value={confirm} onChange={setConfirm} />

          <label className="flex gap-3 text-xs text-slate-300 leading-relaxed cursor-pointer">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(e) => setAgreed(e.target.checked)}
              className="mt-1 accent-emerald-500"
            />
            <span>
              I understand this is a beta product, trading involves risk of capital
              loss, and I authorize daily performance-fee debit via Stripe once approved.
            </span>
          </label>

          {error && (
            <div className="flex gap-2 items-start text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg p-3">
              <AlertCircle size={16} className="mt-0.5 flex-shrink-0" /> {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="btn-primary w-full inline-flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? "Submitting…" : (<><Sparkles size={18} /> Apply for beta</>)}
          </button>

          <div className="text-center text-xs text-slate-500">
            Already have an account?{" "}
            <Link href="/login" className="text-emerald-400 hover:text-emerald-300">Sign in</Link>
          </div>
        </form>
      </motion.div>
    </div>
  );
}

function Field({
  label, value, onChange, type = "text", hint,
}: { label: string; value: string; onChange: (v: string) => void; type?: string; hint?: string }) {
  return (
    <label className="block">
      <div className="flex justify-between items-center mb-1">
        <span className="text-xs text-slate-300 font-medium">{label}</span>
        {hint && <span className="text-[10px] text-slate-500">{hint}</span>}
      </div>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required
        className="w-full bg-slate-950/50 border border-white/10 rounded-lg px-4 py-2.5 text-sm
                   focus:outline-none focus:border-emerald-500/50 focus:ring-2 focus:ring-emerald-500/20
                   transition"
      />
    </label>
  );
}
