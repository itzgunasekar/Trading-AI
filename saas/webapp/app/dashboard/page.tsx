"use client";

import { motion } from "framer-motion";
import { TrendingUp, TrendingDown, Activity, Wallet, ShieldCheck, Clock, DollarSign } from "lucide-react";

type OpenPosition = {
  symbol: string; direction: "BUY"|"SELL"; volume: number;
  open_time: string; entry_price: number; current_price: number; floating_pnl: number;
};
type ClosedTrade = {
  symbol: string; direction: "BUY"|"SELL"; open_time: string; close_time: string; realized_pnl: number;
};

export default function UserDashboard() {
  // TODO: load from /api/user/dashboard
  const account = {
    balance: 0, equity: 0, margin_used: 0, margin_free: 0,
    bot_status: "stopped" as const,
    fees_today: 0, next_fee_at: "—",
  };
  const open: OpenPosition[] = [];
  const recent: ClosedTrade[] = [];

  return (
    <div className="max-w-7xl mx-auto px-6 pt-28 pb-16 space-y-8">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex flex-wrap items-end justify-between gap-4"
      >
        <div>
          <h1 className="text-3xl font-bold">My Trading</h1>
          <p className="text-slate-400 text-sm mt-1">Your live positions and history</p>
        </div>
        <StatusPill status={account.bot_status} />
      </motion.div>

      {/* KPIs */}
      <motion.div
        initial="hidden" animate="show"
        variants={{ hidden:{}, show:{ transition:{ staggerChildren: 0.06 } } }}
        className="grid grid-cols-2 md:grid-cols-4 gap-4"
      >
        <Kpi icon={<Wallet size={18} />} label="Equity" value={`$${account.equity.toFixed(2)}`} />
        <Kpi icon={<DollarSign size={18} />} label="Balance" value={`$${account.balance.toFixed(2)}`} />
        <Kpi icon={<Activity size={18} />} label="Margin used" value={`$${account.margin_used.toFixed(2)}`} />
        <Kpi icon={<Clock size={18} />} label="Fee today" value={`$${account.fees_today.toFixed(2)}`} hint={`Next: ${account.next_fee_at}`} />
      </motion.div>

      {/* Open positions */}
      <Section title="Open positions" subtitle={`${open.length} live`}>
        {open.length === 0 ? (
          <EmptyState msg="No positions open right now. Your bot will open trades when valid setups appear at H1 / D1 bar closes." />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="text-xs text-slate-400 uppercase tracking-wider">
                <tr className="border-b border-white/5">
                  <Th>Symbol</Th><Th>Side</Th><Th align="right">Volume</Th>
                  <Th align="right">Entry</Th><Th align="right">Current</Th>
                  <Th align="right">Floating P&L</Th>
                </tr>
              </thead>
              <tbody>
                {open.map((r, i) => (
                  <tr key={i} className="border-b border-white/5 hover:bg-white/5 transition">
                    <td className="px-4 py-3 font-medium">{r.symbol}</td>
                    <td className="px-4 py-3"><DirPill dir={r.direction} /></td>
                    <td className="px-4 py-3 text-right tabular-nums">{r.volume}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{r.entry_price}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{r.current_price}</td>
                    <td className={`px-4 py-3 text-right tabular-nums font-medium ${r.floating_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      ${r.floating_pnl.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* History */}
      <Section title="Recent closed trades">
        {recent.length === 0 ? (
          <EmptyState msg="No history yet. Closed trades will appear here." />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="text-xs text-slate-400 uppercase tracking-wider">
                <tr className="border-b border-white/5">
                  <Th>Symbol</Th><Th>Side</Th><Th>Opened</Th><Th>Closed</Th>
                  <Th align="right">Realized P&L</Th>
                </tr>
              </thead>
              <tbody>
                {recent.map((r, i) => (
                  <tr key={i} className="border-b border-white/5 hover:bg-white/5 transition">
                    <td className="px-4 py-3 font-medium">{r.symbol}</td>
                    <td className="px-4 py-3"><DirPill dir={r.direction} /></td>
                    <td className="px-4 py-3 text-slate-400">{new Date(r.open_time).toLocaleString()}</td>
                    <td className="px-4 py-3 text-slate-400">{new Date(r.close_time).toLocaleString()}</td>
                    <td className={`px-4 py-3 text-right tabular-nums font-medium ${r.realized_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      ${r.realized_pnl.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* Footer note */}
      <div className="glass rounded-xl p-5 text-xs text-slate-400 flex gap-3">
        <ShieldCheck size={20} className="text-emerald-400 flex-shrink-0" />
        <div>
          Your funds remain in your broker account at all times. We only execute trades with the credentials you provided.
          To stop trading, change your broker password — the bot loses access immediately.
        </div>
      </div>
    </div>
  );
}

function Kpi({ icon, label, value, hint }: { icon: React.ReactNode; label: string; value: string; hint?: string }) {
  return (
    <motion.div
      variants={{ hidden:{ opacity:0, y: 20 }, show:{ opacity:1, y: 0 } }}
      className="glass rounded-xl p-5 hover:bg-white/5 transition"
    >
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-slate-400 uppercase tracking-wider">{label}</span>
        <span className="text-slate-500">{icon}</span>
      </div>
      <div className="text-2xl font-bold tabular-nums">{value}</div>
      {hint && <div className="text-xs text-slate-500 mt-1">{hint}</div>}
    </motion.div>
  );
}

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <motion.section
      initial={{ opacity:0, y: 20 }}
      whileInView={{ opacity:1, y: 0 }}
      viewport={{ once: true }}
    >
      <div className="flex items-end justify-between mb-3">
        <h2 className="text-xl font-semibold">{title}</h2>
        {subtitle && <span className="text-xs text-slate-500">{subtitle}</span>}
      </div>
      <div className="glass-strong rounded-xl overflow-hidden gradient-border">{children}</div>
    </motion.section>
  );
}

function Th({ children, align = "left" }: { children: React.ReactNode; align?: "left"|"right" }) {
  return <th className={`px-4 py-3 font-medium text-${align}`}>{children}</th>;
}

function DirPill({ dir }: { dir: "BUY"|"SELL" }) {
  return dir === "BUY"
    ? <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs bg-emerald-500/15 text-emerald-300"><TrendingUp size={12} /> BUY</span>
    : <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs bg-red-500/15 text-red-300"><TrendingDown size={12} /> SELL</span>;
}

function StatusPill({ status }: { status: string }) {
  const styles: Record<string, string> = {
    running: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    stopped: "bg-slate-700/50 text-slate-300 border-slate-600",
    paused_unpaid: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    paused_admin: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  };
  return (
    <span className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs border ${styles[status] || styles.stopped}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${status === "running" ? "bg-emerald-400 pulse-dot" : "bg-slate-400"}`} />
      Bot: {status.replace("_", " ")}
    </span>
  );
}

function EmptyState({ msg }: { msg: string }) {
  return <div className="px-6 py-16 text-center text-slate-500 text-sm">{msg}</div>;
}
