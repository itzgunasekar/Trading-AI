// Admin panel — protected by middleware (TODO).
// Lists pending signups, lets admin approve / set fee % / pause / reactivate.

import { headers } from "next/headers";

type UserRow = {
  user_id: string;
  email: string;
  status: string;
  created_at: string;
  bot_status: string;
  fee_pct: number;
  broker: string;
  open_trades: number;
  closed_trades: number;
  total_realized_pnl: number;
  total_fees_paid: number;
};

async function loadUsers(): Promise<UserRow[]> {
  // TODO: wire to FastAPI control plane GET /admin/users
  // For now return empty so the page renders.
  return [];
}

export default async function AdminDashboard() {
  const users = await loadUsers();

  return (
    <div className="space-y-8">
      <h1 className="text-3xl font-bold">Admin Dashboard</h1>

      <section>
        <h2 className="text-xl font-semibold mb-4">All users</h2>
        <div className="overflow-x-auto bg-slate-900 rounded-lg">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-800">
              <tr>
                <Th>Email</Th>
                <Th>Status</Th>
                <Th>Bot</Th>
                <Th>Fee %</Th>
                <Th>Broker</Th>
                <Th>Open</Th>
                <Th>Closed</Th>
                <Th>Realized</Th>
                <Th>Fees paid</Th>
                <Th>Actions</Th>
              </tr>
            </thead>
            <tbody>
              {users.length === 0 ? (
                <tr>
                  <td className="px-4 py-6 text-center text-slate-500" colSpan={10}>
                    No users yet. Wire to /admin/users endpoint to populate.
                  </td>
                </tr>
              ) : (
                users.map((u) => <UserRowComp key={u.user_id} user={u} />)
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="text-left px-4 py-2 font-medium">{children}</th>;
}

function UserRowComp({ user }: { user: UserRow }) {
  return (
    <tr className="border-t border-slate-800">
      <td className="px-4 py-3">{user.email}</td>
      <td className="px-4 py-3">
        <StatusPill status={user.status} />
      </td>
      <td className="px-4 py-3">{user.bot_status}</td>
      <td className="px-4 py-3">{user.fee_pct}%</td>
      <td className="px-4 py-3">{user.broker}</td>
      <td className="px-4 py-3">{user.open_trades}</td>
      <td className="px-4 py-3">{user.closed_trades}</td>
      <td className="px-4 py-3">${user.total_realized_pnl.toFixed(2)}</td>
      <td className="px-4 py-3">${user.total_fees_paid.toFixed(2)}</td>
      <td className="px-4 py-3 space-x-2">
        {user.status === "pending" && (
          <button className="bg-emerald-500 text-slate-950 px-2 py-1 rounded text-xs">
            Approve
          </button>
        )}
        {user.status === "approved" && (
          <button className="border border-amber-500 text-amber-400 px-2 py-1 rounded text-xs">
            Pause
          </button>
        )}
        {user.status === "paused_unpaid" && (
          <button className="border border-emerald-500 text-emerald-400 px-2 py-1 rounded text-xs">
            Reactivate
          </button>
        )}
      </td>
    </tr>
  );
}

function StatusPill({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: "bg-slate-700 text-slate-200",
    approved: "bg-emerald-700 text-emerald-100",
    paused: "bg-amber-700 text-amber-100",
    paused_unpaid: "bg-red-700 text-red-100",
    banned: "bg-red-900 text-red-200",
  };
  return (
    <span
      className={`px-2 py-0.5 rounded text-xs ${
        styles[status] || "bg-slate-700"
      }`}
    >
      {status}
    </span>
  );
}
