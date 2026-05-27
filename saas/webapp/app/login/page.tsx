"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import Link from "next/link";
import { LogIn, AlertCircle, ShieldCheck } from "lucide-react";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfa, setMfa] = useState("");
  const [needsMfa, setNeedsMfa] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setSubmitting(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, mfa_code: mfa || undefined }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Login failed");
      if (data.needs_mfa) { setNeedsMfa(true); return; }
      window.location.href = "/dashboard";
    } catch (err: any) {
      setError(err.message);
    } finally { setSubmitting(false); }
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
          <h1 className="text-3xl font-bold mb-2">Welcome back</h1>
          <p className="text-slate-400 text-sm">Sign in to manage your trading</p>
        </div>

        <form onSubmit={handleSubmit} className="glass-strong rounded-2xl p-8 space-y-5 gradient-border">
          <Field label="Email" type="email" value={email} onChange={setEmail} />
          <Field label="Password" type="password" value={password} onChange={setPassword} />
          {needsMfa && (
            <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }}>
              <Field label="6-digit MFA code" type="text" value={mfa} onChange={setMfa} hint="From your authenticator" />
              <p className="text-xs text-slate-500 mt-2 flex items-center gap-1">
                <ShieldCheck size={12} /> Two-factor required for your account
              </p>
            </motion.div>
          )}

          {error && (
            <div className="flex gap-2 items-start text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg p-3">
              <AlertCircle size={16} className="mt-0.5 flex-shrink-0" /> {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="btn-primary w-full inline-flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {submitting ? "Signing in…" : (<><LogIn size={18} /> Sign in</>)}
          </button>

          <div className="text-center text-xs text-slate-500">
            Don&apos;t have an account?{" "}
            <Link href="/signup" className="text-emerald-400 hover:text-emerald-300">Apply for beta</Link>
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
                   focus:outline-none focus:border-emerald-500/50 focus:ring-2 focus:ring-emerald-500/20 transition"
      />
    </label>
  );
}
