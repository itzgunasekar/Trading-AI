# Deployment Guide — D1 Portfolio SaaS

End-to-end recipe to take the project from this repo to a live beta with real users.

## Prerequisites (do these BEFORE writing more code)

1. **Open IC Markets IB account** (or alternative broker IB) — confirms revenue stream A
2. **Apply for Stripe** — get test API keys; live keys once incorporated
3. **Talk to a fintech lawyer** — 30-min consult, get ToS + risk disclosure template
4. **Choose a domain** — e.g., `d1portfolio.app`
5. **Set up business email** — `support@d1portfolio.app` (Google Workspace or Zoho)

## Local development setup

### Database (Postgres via Docker)

```bash
docker run -d --name d1db \
  -e POSTGRES_USER=d1user \
  -e POSTGRES_PASSWORD=devpass \
  -e POSTGRES_DB=d1saas \
  -p 5432:5432 \
  postgres:16

psql postgresql://d1user:devpass@localhost:5432/d1saas -f saas/db/schema.sql
psql postgresql://d1user:devpass@localhost:5432/d1saas -f saas/db/seed.sql
```

### Control plane (FastAPI)

```bash
cd saas/control_plane
cp .env.example .env

# Generate the master KEK
python -c "import secrets; print('D1BOT_KEK_HEX=' + secrets.token_hex(32))" >> .env
python -c "import secrets; print('JWT_SECRET=' + secrets.token_hex(32))" >> .env

# Fill in Stripe test keys and DATABASE_URL in .env

pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

Open `http://localhost:8000/docs` to browse the API.

### Frontend (Next.js)

```bash
cd saas/webapp
npm install
npm run dev   # http://localhost:3000
```

The webapp's `next.config.mjs` proxies `/api/*` to FastAPI on `:8000`.

## Production deployment

### 1. Database — Supabase (free tier)
- Create project at supabase.com
- Get the connection string (the "session pooler" URL works for serverless)
- Apply `schema.sql` via the Supabase SQL editor

### 2. Control plane — Render or Railway
- New Web Service → Connect repo → root `saas/control_plane`
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
- Set all env vars from `.env.example` (use Render's secrets management)
- Set `ENV=production` (disables /docs)

### 3. Frontend — Vercel
- Import `saas/webapp` directory
- Environment variable: `NEXT_PUBLIC_API_URL=https://your-render-app.onrender.com`
- Deploy

### 4. Bot containers — Contabo VPS (or Fly.io)
- Each approved user gets one VPS (~$5/mo)
- Build `d1bot:latest` Docker image (see `bot_farm/Dockerfile`)
- Image must include the bot source from `D:\Ajith\diff_ea_ai\d1_portfolio_*.py`
- Run via `bot_farm/provision.py`

### 5. Cron job — daily fee calculation
- Render cron job OR a separate small worker
- Schedule: `0 0 * * *` (00:00 UTC)
- Command: `python -m billing.cron_daily`

### 6. Stripe webhook
- Configure endpoint at `https://api.d1portfolio.app/billing/webhook`
- Subscribe to events: `invoice.payment_succeeded`, `invoice.payment_failed`
- Copy the signing secret to `STRIPE_WEBHOOK_SECRET`

### 7. Cloudflare in front
- Proxy all DNS through Cloudflare
- Enable WAF rules: bot fight mode, rate-limit `/api/auth/login` to 5/min/IP
- Force HTTPS, HSTS preload

## Operating checklist (first 2 weeks)

- [ ] Verify Stripe webhook is reaching production by checking `audit_log`
- [ ] Manually approve 3 test users (yourself + 2 friends)
- [ ] Walk through full signup → broker creds → first trade → first fee debit
- [ ] Confirm `analytics/by_strategy.csv` per user populates correctly
- [ ] Run a chaos drill: kill one user's bot container, confirm SL/TP at broker still protect their open positions
- [ ] Verify daily cron at midnight UTC creates Fee rows + Stripe invoices
- [ ] Restore database from Supabase backup at least once
- [ ] Rotate the KEK once (verify zero-downtime)

## Security checklist before public launch

- [ ] Penetration test by 3rd party (Cobalt, HackerOne)
- [ ] SAST scan (Semgrep) on all repos
- [ ] DAST scan (OWASP ZAP) against staging
- [ ] Bug bounty program live
- [ ] Cyber-liability insurance ($1M+)
- [ ] Incident response runbook written
- [ ] All staff with admin access have MFA + hardware key
- [ ] Audit log shipped to immutable storage (S3 object lock)
- [ ] Lawyer-reviewed ToS, Privacy Policy, Risk Disclosure published
