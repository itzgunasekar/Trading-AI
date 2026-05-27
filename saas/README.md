# D1 Portfolio Bot — SaaS Shell

Multi-tenant commercialization layer for the existing single-user bot.

## Directory map

```
saas/
├── README.md                      # this file
├── control_plane/                 # FastAPI service — admin / billing / auth
│   ├── api/                       # HTTP route handlers
│   ├── auth/                      # signup, login, MFA, JWT
│   ├── admin/                     # approval, fee%, pause/resume
│   ├── billing/                   # Stripe webhooks, daily fee cron
│   └── security/                  # encryption helpers, audit log
│
├── db/
│   ├── schema.sql                 # Postgres DDL + row-level security
│   ├── seed.sql                   # admin user, default config
│   └── migrations/                # Alembic migrations
│
├── bot_farm/                      # provision per-user bot containers
│   ├── provision.py               # spin up container for approved user
│   ├── teardown.py                # graceful close + remove
│   └── Dockerfile                 # MT5 + bot image
│
└── webapp/                        # Next.js user/admin frontend
    ├── app/                       # routes (App Router)
    ├── components/                # shared UI
    └── lib/                       # API client, auth helpers
```

## Architecture (one-line summary)

User browser → Next.js (Vercel) → FastAPI control plane (Render) → Postgres (Supabase) + bot containers (Fly.io / Contabo)

**Strategy code never touches the control plane or database — it lives only in the bot containers running on private network.**

## What stays from the existing bot (DO NOT BREAK)

The single-user bot at `D:\Ajith\diff_ea_ai\d1_portfolio_*.py` is **production-running** on the admin's own account. The SaaS work happens in parallel here without touching those files until:

1. SaaS shell is built and tested with mock data
2. We refactor the bot to accept a `UserBotContext` (Phase 2)
3. The new multi-user bot replaces the single-user one on the admin's container too

## Phase 1 MVP scope (this directory)

| Component | Status |
|-----------|--------|
| `db/schema.sql` — full Postgres DDL | TODO |
| `control_plane/` — FastAPI app | TODO |
| `control_plane/security/encryption.py` — AES-256-GCM | TODO |
| `webapp/` — Next.js signup, dashboard, admin panel | TODO |
| `bot_farm/Dockerfile` — containerized bot | TODO (Phase 2) |
| Stripe integration | TODO |
| Manual provision/teardown ops runbook | TODO |

## Deployment targets (free tier MVP)

- **Frontend**: Vercel (Next.js native)
- **Control plane**: Render or Railway (FastAPI)
- **Database**: Supabase (Postgres + Auth + RLS)
- **Bot containers**: Contabo VPS $5/mo each (NOT free at scale)
- **Secrets**: Doppler free / Vault OSS
- **Payments**: Stripe (or Razorpay for India)
- **Email**: Resend
- **Logs / errors**: Better Stack (Logtail) + Sentry

## Running locally (once built)

```bash
# Terminal 1 — database (Docker)
docker run -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres:16
psql -h localhost -U postgres -f saas/db/schema.sql

# Terminal 2 — FastAPI control plane
cd saas/control_plane
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000

# Terminal 3 — Next.js frontend
cd saas/webapp
npm install
npm run dev   # serves on http://localhost:3000
```
