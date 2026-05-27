"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import {
  TrendingUp, Shield, Bot, Lock, Zap, BarChart3,
  Globe, CheckCircle2, ChevronRight, Sparkles,
} from "lucide-react";

const fadeUp = {
  initial: { opacity: 0, y: 24 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "-80px" },
  transition: { duration: 0.6, ease: "easeOut" },
};

export default function LandingPage() {
  return (
    <>
      {/* HERO */}
      <section className="relative pt-32 pb-24 overflow-hidden">
        <div className="absolute inset-0 grid-bg opacity-30 pointer-events-none" />
        <div className="absolute top-20 left-1/2 -translate-x-1/2 w-[800px] h-[400px] rounded-full blur-3xl bg-gradient-to-r from-emerald-500/20 via-cyan-500/20 to-purple-500/20 pointer-events-none" />

        <div className="relative max-w-6xl mx-auto px-6 text-center">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="inline-flex items-center gap-2 px-3 py-1 rounded-full glass text-xs text-slate-300 mb-8"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot" />
            Beta access — applications open
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.1 }}
            className="text-5xl md:text-7xl font-bold tracking-tight leading-[1.05] mb-6"
          >
            Institutional-grade trading,<br />
            <span className="gradient-text">on autopilot.</span>
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.2 }}
            className="text-xl text-slate-300 max-w-2xl mx-auto mb-10"
          >
            12 quantitative strategies. 21 instruments across Forex, Metals & Indices.
            8-year out-of-sample validated. Runs on your broker — you keep your money.
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.4 }}
            className="flex flex-col sm:flex-row gap-4 justify-center"
          >
            <Link href="/signup" className="btn-primary inline-flex items-center gap-2 justify-center">
              <Sparkles size={18} /> Apply for beta access
            </Link>
            <Link href="#how-it-works" className="btn-secondary inline-flex items-center gap-2 justify-center">
              See how it works <ChevronRight size={18} />
            </Link>
          </motion.div>

          {/* Hero stats */}
          <motion.div
            initial={{ opacity: 0, y: 40 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.6 }}
            className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-20 max-w-3xl mx-auto"
          >
            {[
              { v: "12", l: "Strategies" },
              { v: "21", l: "Instruments" },
              { v: "8y", l: "Backtested" },
              { v: "≈60%", l: "Avg Win Rate" },
            ].map(({ v, l }) => (
              <div key={l} className="glass rounded-lg p-4">
                <div className="text-3xl font-bold gradient-text">{v}</div>
                <div className="text-xs text-slate-400 uppercase tracking-wider mt-1">{l}</div>
              </div>
            ))}
          </motion.div>
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section id="how-it-works" className="py-24 relative">
        <div className="max-w-6xl mx-auto px-6">
          <motion.div {...fadeUp} className="text-center mb-16">
            <h2 className="text-4xl md:text-5xl font-bold mb-4">
              From signup to first trade in <span className="gradient-text">under 10 minutes</span>
            </h2>
            <p className="text-slate-400 max-w-2xl mx-auto">
              You keep total control of your money. We just execute trades on your behalf.
            </p>
          </motion.div>

          <div className="grid md:grid-cols-3 gap-6">
            {[
              { icon: <Shield size={24} />, n: "01", t: "Apply & get approved", d: "Every beta application is reviewed by a human. We verify identity and acceptance of risks before granting access." },
              { icon: <Lock size={24} />, n: "02", t: "Connect your broker", d: "Enter your MT5 credentials inside our app. Stored with AES-256-GCM, per-user keys, never visible to staff." },
              { icon: <Bot size={24} />, n: "03", t: "Bot trades for you", d: "We provision a private execution environment. Your funds stay at the broker — we just send orders." },
            ].map((step, i) => (
              <motion.div
                key={step.n}
                initial={{ opacity: 0, y: 30 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.1, duration: 0.6 }}
                className="glass rounded-xl p-6 relative gradient-border"
              >
                <div className="absolute top-4 right-4 text-5xl font-black text-white/5">{step.n}</div>
                <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-emerald-500/20 to-cyan-500/20 grid place-items-center text-emerald-400 mb-4">
                  {step.icon}
                </div>
                <h3 className="text-xl font-semibold mb-2">{step.t}</h3>
                <p className="text-slate-400 text-sm">{step.d}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* STRATEGIES */}
      <section id="strategies" className="py-24 relative">
        <div className="max-w-6xl mx-auto px-6">
          <motion.div {...fadeUp} className="text-center mb-16">
            <h2 className="text-4xl md:text-5xl font-bold mb-4">
              Diversified across <span className="gradient-text">12 quantitative strategies</span>
            </h2>
            <p className="text-slate-400 max-w-2xl mx-auto">
              No single strategy ever has all the capital. Edges from trend, mean-reversion, breakouts and pattern recognition combine to smooth the equity curve.
            </p>
          </motion.div>

          <motion.div {...fadeUp} className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 max-w-5xl mx-auto">
            {[
              { name: "Donchian 20", style: "Trend" },
              { name: "Momentum 60", style: "Trend" },
              { name: "Inside Bar", style: "Breakout" },
              { name: "RSI(2) Connors", style: "Mean Reversion" },
              { name: "Bollinger Extreme", style: "Mean Reversion" },
              { name: "3-Day Reverse", style: "Mean Reversion" },
              { name: "NR7 Breakout", style: "Volatility" },
              { name: "London ORB", style: "Session" },
              { name: "H1 Donchian", style: "Intraday" },
              { name: "H1 Momentum", style: "Intraday" },
              { name: "H1 RSI(2)", style: "Intraday" },
              { name: "Consensus", style: "Multi-signal" },
            ].map((s) => (
              <div key={s.name} className="glass rounded-lg px-4 py-3 hover:bg-white/5 transition">
                <div className="text-sm font-semibold">{s.name}</div>
                <div className="text-xs text-slate-500 mt-0.5">{s.style}</div>
              </div>
            ))}
          </motion.div>

          <motion.div {...fadeUp} className="mt-12 grid md:grid-cols-3 gap-4 max-w-4xl mx-auto">
            {[
              { icon: <Globe size={20} />, label: "21 instruments", note: "Forex majors, metals, indices, JPY crosses" },
              { icon: <BarChart3 size={20} />, label: "Pre-trade quality score", note: "Filters low-confidence setups" },
              { icon: <Zap size={20} />, label: "SL → BE migration", note: "Locks profit at 50% to TP" },
            ].map((f) => (
              <div key={f.label} className="glass rounded-lg p-4 flex gap-3">
                <div className="text-emerald-400 mt-0.5">{f.icon}</div>
                <div>
                  <div className="font-semibold text-sm">{f.label}</div>
                  <div className="text-xs text-slate-400">{f.note}</div>
                </div>
              </div>
            ))}
          </motion.div>
        </div>
      </section>

      {/* SECURITY */}
      <section id="security" className="py-24 relative">
        <div className="max-w-6xl mx-auto px-6">
          <motion.div {...fadeUp} className="grid md:grid-cols-2 gap-12 items-center">
            <div>
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full glass text-xs text-slate-300 mb-4">
                <Lock size={12} className="text-emerald-400" /> Security
              </div>
              <h2 className="text-4xl font-bold mb-4">
                Your credentials,<br />
                <span className="gradient-text">cryptographically isolated</span>
              </h2>
              <p className="text-slate-400 mb-6">
                We follow the same playbook fintech regulators expect. No staff member can read your broker password. No SQL injection or stolen database dump will leak it either.
              </p>
              <ul className="space-y-3">
                {[
                  "AES-256-GCM, per-user data encryption keys",
                  "TOTP MFA mandatory after first login",
                  "Argon2id password hashing",
                  "Row-level security in Postgres",
                  "Immutable audit log of every privileged action",
                  "TLS 1.3 + HSTS preloaded",
                ].map((line) => (
                  <li key={line} className="flex gap-3 items-start">
                    <CheckCircle2 size={18} className="text-emerald-400 mt-0.5 flex-shrink-0" />
                    <span className="text-slate-300 text-sm">{line}</span>
                  </li>
                ))}
              </ul>
            </div>
            <motion.div
              initial={{ opacity: 0, scale: 0.9 }}
              whileInView={{ opacity: 1, scale: 1 }}
              viewport={{ once: true }}
              transition={{ duration: 0.7 }}
              className="glass-strong rounded-2xl p-6 gradient-border"
            >
              <pre className="text-xs text-slate-300 font-mono overflow-x-auto">
{`# Your MT5 password as we see it:
b'\\x4f\\xa1\\xcc\\x7e\\xd2\\x88...'
                  ↑
       ciphertext (useless without DEK)

# DEK encrypted by KEK in Vault.
# Vault unlocks only with quorum approval.
# No engineer has standalone access.`}
              </pre>
              <div className="mt-4 pt-4 border-t border-white/10 text-xs text-slate-400">
                <strong className="text-white">Honest disclosure:</strong> No system is unhackable — Apple, Google, banks have all been breached. We follow industry-best practices, log everything, and carry cyber-liability insurance.
              </div>
            </motion.div>
          </motion.div>
        </div>
      </section>

      {/* PRICING */}
      <section id="pricing" className="py-24 relative">
        <div className="max-w-4xl mx-auto px-6 text-center">
          <motion.div {...fadeUp}>
            <h2 className="text-4xl md:text-5xl font-bold mb-4">
              You pay only when <span className="gradient-text">you make money</span>
            </h2>
            <p className="text-slate-400 mb-12">
              Performance-based fee debited daily via Stripe. No fixed subscription.
            </p>
          </motion.div>

          <motion.div {...fadeUp} className="glass-strong rounded-2xl p-10 gradient-border">
            <div className="text-6xl font-bold gradient-text mb-2">20%</div>
            <div className="text-slate-300 mb-8">of your <em>realized</em> daily profit. Period.</div>

            <div className="grid md:grid-cols-3 gap-4 text-left text-sm">
              {[
                { t: "No upfront fee", d: "$0 to start. Pay only when bot earns for you." },
                { t: "Daily settlement", d: "Calculated each day at 00:00 UTC from realized closes." },
                { t: "Failed payment safe", d: "Bot pauses, existing positions keep server-side SL/TP." },
              ].map((p) => (
                <div key={p.t} className="glass rounded-lg p-4">
                  <div className="font-semibold mb-1">{p.t}</div>
                  <div className="text-slate-400 text-xs">{p.d}</div>
                </div>
              ))}
            </div>

            <Link href="/signup" className="btn-primary inline-flex items-center gap-2 mt-10">
              <Sparkles size={18} /> Apply for beta
            </Link>
          </motion.div>
        </div>
      </section>

      {/* DISCLOSURE */}
      <section className="py-16 border-t border-white/5">
        <div className="max-w-3xl mx-auto px-6">
          <motion.div {...fadeUp} className="glass rounded-xl p-6 text-sm space-y-3">
            <div className="flex items-center gap-2 font-semibold text-amber-400">
              <Shield size={18} /> Risk disclosure
            </div>
            <ul className="space-y-2 text-slate-300 list-disc list-inside">
              <li>Trading derivatives involves substantial risk of loss. You can lose more than you deposit.</li>
              <li>Past performance shown in our backtests does NOT guarantee future results.</li>
              <li>The bot is in beta. We may pause or stop services at any time.</li>
              <li>Tax obligations on profits are entirely yours to manage.</li>
              <li>This is not investment advice. We do not custody your funds.</li>
            </ul>
          </motion.div>
        </div>
      </section>
    </>
  );
}
