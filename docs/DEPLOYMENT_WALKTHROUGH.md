# Deployment Walkthrough — Every Click, Every Command

**You sit at the keyboard. I tell you exactly what to do.** No prior experience required.

This takes you from "I have a Windows computer" to "my SaaS is live on the internet". Total time: ~6 hours spread across 1-2 weekends.

> **⚠️ Don't start this walkthrough until you've completed [PRE_LAUNCH_CHECKLIST.md](./PRE_LAUNCH_CHECKLIST.md).** Deploying before validation = paying to host an unvalidated product.

---

# Part 1: Install required tools (~30 minutes)

You'll install these on your Windows computer. Each install is "click Next, Next, Finish" — they don't need any configuration.

## 1.1 — Install Python

You already have Python 3.14 installed (we used it for the bot). Verify by opening PowerShell:

1. Press `Windows key + R`, type `powershell`, press Enter
2. Type: `python --version` → press Enter
3. You should see something like `Python 3.14.x`

✅ If yes, skip to 1.2.
❌ If "command not found", download from `https://www.python.org/downloads/` → click "Download Python 3.x" → run installer → **CHECK "Add Python to PATH"** at the bottom → click Install Now.

## 1.2 — Install Node.js (for the webapp)

1. Open browser → `https://nodejs.org/`
2. Click the **LTS** version button (left button, says "Recommended for most users")
3. Run the downloaded installer
4. Click Next on every screen, accept defaults, finish
5. **Close and reopen PowerShell**
6. Verify: `node --version` should show something like `v22.x.x` and `npm --version` shows `10.x.x`

## 1.3 — Install Docker Desktop (for local Postgres database)

1. Open browser → `https://www.docker.com/products/docker-desktop/`
2. Click "Download for Windows"
3. Run the installer (~500MB)
4. **It will ask you to restart your computer** — let it
5. After restart, Docker Desktop opens. Accept the terms.
6. You may need to enable WSL2 — it'll guide you through this
7. Verify: open PowerShell, type `docker --version` → should show `Docker version 27.x.x`

## 1.4 — Install Git

1. Browser → `https://git-scm.com/download/win`
2. Run installer → click Next on every screen → Finish
3. Verify in PowerShell: `git --version` shows `git version 2.xx.x`

## 1.5 — Install VS Code (a friendly code editor)

1. Browser → `https://code.visualstudio.com/`
2. Download for Windows → run installer
3. Accept defaults, finish

## 1.6 — Create a GitHub account

1. Browser → `https://github.com/signup`
2. Use your real email
3. Pick a strong password
4. Verify your email
5. **Save your username** — you'll use it in step 3

✅ **Check at end of Part 1:** open PowerShell and run all of these — each should print a version:
```
python --version
node --version
npm --version
docker --version
git --version
```

If all 5 work, you're ready. If any fails, fix that one before continuing.

---

# Part 2: Run the SaaS locally on your computer (~90 minutes)

We'll get the database, backend, and frontend running on your machine first. This confirms everything works before paying for cloud hosting.

## 2.1 — Start the Postgres database (Docker)

1. Open PowerShell
2. **Make sure Docker Desktop is running** (look for the whale icon in the system tray, bottom-right)
3. Copy-paste this command (whole thing, one line):

```powershell
docker run -d --name d1db -e POSTGRES_USER=d1user -e POSTGRES_PASSWORD=devpass123 -e POSTGRES_DB=d1saas -p 5432:5432 postgres:16
```

4. Press Enter. You'll see a long string of characters (the container ID). That means it worked.

5. **Verify it's running**: `docker ps` → you should see `d1db` in the list.

6. **Apply the database schema:**

```powershell
docker cp D:\Ajith\diff_ea_ai\saas\db\schema.sql d1db:/tmp/schema.sql
docker exec -it d1db psql -U d1user -d d1saas -f /tmp/schema.sql
```

You'll see a long output ending with no errors. If you see `CREATE TABLE`, `CREATE INDEX`, `CREATE POLICY` messages, it worked.

**If you see errors**, copy them and paste back to me — most likely a typo in the docker command.

## 2.2 — Set up the FastAPI backend

1. In PowerShell, navigate to the control plane:

```powershell
cd D:\Ajith\diff_ea_ai\saas\control_plane
```

2. Install Python dependencies (this takes 2-3 minutes):

```powershell
pip install -r requirements.txt
```

3. Generate your secret keys (run BOTH lines):

```powershell
python -c "import secrets; print('D1BOT_KEK_HEX=' + secrets.token_hex(32))"
python -c "import secrets; print('JWT_SECRET=' + secrets.token_hex(32))"
```

4. Each prints a line. **Copy both lines.**

5. Create a `.env` file in `D:\Ajith\diff_ea_ai\saas\control_plane\` with this exact content (replace `xxx` with the values you just generated):

```
D1BOT_KEK_HEX=xxx_paste_first_value_here_xxx
JWT_SECRET=xxx_paste_second_value_here_xxx
DATABASE_URL=postgresql://d1user:devpass123@localhost:5432/d1saas
STRIPE_SECRET_KEY=sk_test_placeholder
STRIPE_WEBHOOK_SECRET=whsec_placeholder
ALLOWED_HOSTS=localhost,127.0.0.1
CORS_ORIGINS=http://localhost:3000
ENV=development
```

6. **Start the backend:**

```powershell
$env:D1BOT_KEK_HEX="paste_kek_value_again_here"
$env:JWT_SECRET="paste_jwt_value_again_here"
$env:DATABASE_URL="postgresql://d1user:devpass123@localhost:5432/d1saas"
uvicorn api.main:app --reload --port 8000
```

7. You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

8. **Verify in browser**: open `http://localhost:8000/health`
   - Should show: `{"status":"ok","service":"d1-saas-control-plane",...}`
9. **Browse the API**: open `http://localhost:8000/docs` — you'll see Swagger UI with all 23 endpoints.

10. **Leave this PowerShell window open and running.** Don't close it.

## 2.3 — Set up the Next.js frontend

1. **Open a SECOND PowerShell window** (don't close the first one — it's running the backend)

2. Navigate to the webapp:

```powershell
cd D:\Ajith\diff_ea_ai\saas\webapp
```

3. Install dependencies (~5 minutes, downloads a lot):

```powershell
npm install
```

You'll see lots of output. Wait for the prompt to come back. **Warnings are OK**, errors are not.

4. **Start the frontend:**

```powershell
npm run dev
```

5. After ~10 seconds you'll see:
```
▲ Next.js 15.x.x
- Local:        http://localhost:3000
```

6. **Open browser:** `http://localhost:3000`
7. You should see the dark landing page with the animated hero.

✅ **At this point you have a full local stack running:**
   - Postgres database in Docker
   - FastAPI backend on `:8000` (Window 1)
   - Next.js frontend on `:3000` (Window 2)

## 2.4 — Test the signup flow

1. In the browser, click **"Apply for beta access"** in the top nav
2. Enter:
   - Email: `test@example.com`
   - Password: `MyTestPass123!`
   - Confirm: `MyTestPass123!`
   - Check the agreement box
3. Click "Apply for beta"
4. **You should see**: "Application received!" with a success animation

5. **Verify in database:** open a THIRD PowerShell window:

```powershell
docker exec -it d1db psql -U d1user -d d1saas -c "SELECT user_id, email, status FROM users;"
```

You should see your test@example.com user with status `pending`. 🎉

## 2.5 — Approve the user as admin

For now, do it manually via SQL (the admin UI exists but needs admin login which we'll set up later):

```powershell
docker exec -it d1db psql -U d1user -d d1saas -c "UPDATE users SET status='approved', approved_at=NOW() WHERE email='test@example.com';"
```

Now log in with `test@example.com` / `MyTestPass123!` at `http://localhost:3000/login` — you should land on the dashboard.

✅ **End of Part 2.** You now have a working SaaS running entirely on your computer. Take a screenshot — this is a milestone.

---

# Part 3: Deploy to free cloud (~2 hours)

We'll move the local setup to free-tier cloud services so it's accessible from anywhere.

## 3.1 — Push code to GitHub (so cloud services can deploy from it)

1. **Open VS Code**
2. File → Open Folder → select `D:\Ajith\diff_ea_ai`
3. View → Terminal (or press `Ctrl + ~`)
4. In the terminal, run these one at a time:

```powershell
git config --global user.email "your@email.com"
git config --global user.name "Your Name"
```

5. **Create a NEW PRIVATE repository on GitHub:**
   - Browser → `https://github.com/new`
   - Name: `d1-portfolio-saas`
   - **Set to PRIVATE** ← critical, do NOT make this public
   - Don't initialize with README
   - Click "Create repository"

6. **Copy the commands GitHub shows you under "push an existing repository from the command line"** — they look like:
```
git remote add origin https://github.com/YOUR_USERNAME/d1-portfolio-saas.git
git branch -M main
git push -u origin main
```

7. Back in VS Code terminal, init and push:

```powershell
cd D:\Ajith\diff_ea_ai
git init
echo "node_modules" > .gitignore
echo "__pycache__" >> .gitignore
echo "*.pyc" >> .gitignore
echo ".env" >> .gitignore
echo ".next" >> .gitignore
echo "*.pid" >> .gitignore
git add .
git commit -m "Initial SaaS"
```

8. Then paste the 3 commands from GitHub (with YOUR username).

9. **Verify in browser:** refresh your GitHub repo URL — all the files should now be there.

## 3.2 — Deploy the database to Supabase (free)

1. Browser → `https://supabase.com/`
2. Click "Start your project" → sign up with GitHub (easiest)
3. After signup, click "New project"
4. Fill in:
   - Name: `d1-portfolio`
   - Database password: **generate one and SAVE IT** (1Password / paper). You can't recover it.
   - Region: pick the closest to you (`Asia Pacific (Mumbai)` if in India)
   - Pricing plan: **Free**
5. Click "Create new project" → wait 2-3 minutes for setup
6. Once ready, click **"Project Settings"** (gear icon, bottom-left) → **"Database"**
7. Find "Connection string" → click "URI" tab → **copy the entire connection string** (looks like `postgresql://postgres:[YOUR-PASSWORD]@db.xxxx.supabase.co:5432/postgres`)
8. Replace `[YOUR-PASSWORD]` with the password you saved → save this full string somewhere
9. **Apply schema:** in Supabase dashboard, click "SQL Editor" (left sidebar) → "New query"
10. Open `D:\Ajith\diff_ea_ai\saas\db\schema.sql` in VS Code → **copy ALL of it** → paste into Supabase SQL Editor → click "Run"
11. You'll see green ✓ messages for each CREATE statement

✅ Your production database now exists.

## 3.3 — Deploy the FastAPI backend to Render (free tier)

1. Browser → `https://render.com/`
2. Sign up with GitHub
3. Click "New +" → "Web Service"
4. Connect your `d1-portfolio-saas` repo
5. Fill in:
   - Name: `d1-portfolio-api`
   - Region: closest to you
   - Branch: `main`
   - Root Directory: `saas/control_plane`
   - Runtime: `Python 3`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
   - Plan: **Free**

6. Scroll down → "Environment Variables" → click "Add Environment Variable" for each:

| KEY | VALUE |
|-----|-------|
| `D1BOT_KEK_HEX` | (the value from step 2.2.3) |
| `JWT_SECRET` | (the value from step 2.2.3) |
| `DATABASE_URL` | (the Supabase connection string from 3.2.8) |
| `ALLOWED_HOSTS` | `d1-portfolio-api.onrender.com` |
| `CORS_ORIGINS` | `https://d1-portfolio.vercel.app` (placeholder — we'll update in 3.4) |
| `ENV` | `production` |
| `STRIPE_SECRET_KEY` | `sk_test_placeholder_for_now` |
| `STRIPE_WEBHOOK_SECRET` | `whsec_placeholder_for_now` |

7. Click "Create Web Service" — Render starts building. Takes ~5 minutes.

8. When it's done, you get a URL like `https://d1-portfolio-api.onrender.com`. **Save it.**

9. **Verify:** open `https://d1-portfolio-api.onrender.com/health` — should return JSON with `"status":"ok"`.

## 3.4 — Deploy the frontend to Vercel (free)

1. Browser → `https://vercel.com/signup`
2. Sign up with GitHub
3. Click "Add New..." → "Project"
4. Import your `d1-portfolio-saas` repo
5. Configure:
   - Framework Preset: `Next.js` (auto-detected)
   - **Root Directory**: click "Edit" → enter `saas/webapp`
   - Build Command: leave default (`npm run build`)
6. **Environment Variables**: add ONE:

| KEY | VALUE |
|-----|-------|
| `NEXT_PUBLIC_API_URL` | (the Render URL from 3.3.8 — e.g. `https://d1-portfolio-api.onrender.com`) |

7. Click "Deploy" — takes ~3 minutes
8. You get a URL like `https://d1-portfolio-saas.vercel.app`. **Save it.**

9. **Update Render with the real Vercel URL:**
   - Back in Render → your service → Environment → edit `CORS_ORIGINS`
   - Change value to: `https://d1-portfolio-saas.vercel.app` (use your actual Vercel URL)
   - Save → Render redeploys automatically

10. **Verify the full stack:** open `https://d1-portfolio-saas.vercel.app` — the landing page should load.

11. Try the signup flow — it should hit your Render backend, which talks to Supabase. Check your Supabase Table Editor → `users` table — your new signup should appear.

## 3.5 — Connect a custom domain (optional but recommended)

1. Buy a domain at Namecheap, GoDaddy, or Cloudflare Registrar (~$15/year)
2. In Vercel: Project → Settings → Domains → add your domain
3. Vercel shows you DNS records to add → copy them
4. In your registrar's DNS settings, add those records
5. Wait 5-30 minutes for DNS to propagate
6. SSL is automatic via Vercel

✅ **End of Part 3.** You have a publicly accessible SaaS skeleton on the internet.

---

# Part 4: Connect Stripe / Razorpay (~1 hour, once payments KYC is done)

Skip this until **PRE_LAUNCH_CHECKLIST Step 2** is complete.

## 4.1 — Get Stripe test API keys (or Razorpay)

1. Stripe Dashboard → Developers → API Keys
2. Copy:
   - "Publishable key" (starts `pk_test_...`)
   - "Secret key" (click reveal, starts `sk_test_...`)

## 4.2 — Add a webhook endpoint in Stripe

1. Stripe Dashboard → Developers → Webhooks → "Add endpoint"
2. URL: `https://d1-portfolio-api.onrender.com/billing/webhook` (your Render URL + `/billing/webhook`)
3. Listen to events: **select "Hosted endpoint"**
4. Select these events:
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`
   - `customer.subscription.deleted`
5. Click "Add endpoint"
6. On the resulting page, click "Reveal" next to "Signing secret" → copy (`whsec_...`)

## 4.3 — Update Render env vars with real Stripe keys

1. Render → your service → Environment
2. Update:
   - `STRIPE_SECRET_KEY` = `sk_test_...` (the one from 4.1)
   - `STRIPE_WEBHOOK_SECRET` = `whsec_...` (the one from 4.2.6)
3. Save → Render redeploys

## 4.4 — Test a billing setup flow

(This requires implementing the Stripe SetupIntent component in the dashboard — currently a placeholder. We can add it once you've validated everything else works.)

---

# Part 5: Provision a real user's bot (~30 minutes per user)

For Phase 1 MVP, you'll do this **manually** for each approved user. Automation comes in Phase 2.

## 5.1 — Buy a Contabo VPS (~$5/month per user)

1. Browser → `https://contabo.com/en/vps/`
2. Pick "VPS S" (~$5.49/month) — 4GB RAM is plenty for one MT5 + bot
3. OS: Windows Server 2022 (so MT5 runs natively, no Wine)
4. Pay, wait ~30 min for provisioning
5. You'll receive an email with the IP, username, and password

## 5.2 — Set up the VPS

1. On your computer, press `Win + R` → type `mstsc` → Enter (Remote Desktop)
2. Enter the VPS IP, click Connect, accept the certificate
3. Log in with the credentials from the email
4. Inside the VPS:
   - Open Edge browser → download MetaTrader 5 from your broker's site
   - Install MT5
   - Log in with the USER'S MT5 credentials (you'll have these from their signup)
   - Open Algo Trading button (toolbar)
5. Download Python 3.14 → install → check "Add to PATH"
6. Download `D:\Ajith\diff_ea_ai\d1_portfolio_*.py` files from your GitHub to the VPS (use Git on the VPS, or copy-paste)
7. Update `d1_portfolio_config.py` on the VPS:
   - `ACCT_NO` = user's MT5 account number
   - Other settings can stay default
8. Open PowerShell on VPS:
```powershell
cd C:\bot
pip install MetaTrader5 numpy
python d1_portfolio_bot.py
```

✅ The bot is now trading on the user's account from the VPS.

## 5.3 — Track it on YOUR admin dashboard

Currently the production bot writes locally on the VPS. To pipe its activity into the SaaS admin dashboard, you'd need to refactor the bot to write to the Supabase database instead of local files. That's a Phase 2 task — for Phase 1 MVP, you can check each VPS individually.

---

# Status check after this walkthrough

| Item | Status |
|------|--------|
| Local Postgres + FastAPI + Next.js running | Part 2 done = ✅ |
| Code on private GitHub | Part 3.1 done = ✅ |
| Production DB on Supabase | Part 3.2 done = ✅ |
| Production API on Render | Part 3.3 done = ✅ |
| Production frontend on Vercel | Part 3.4 done = ✅ |
| Stripe webhook connected | Part 4 done = ✅ (after PRE_LAUNCH step 2) |
| First real user provisioned | Part 5 done = ✅ (when first user signs up) |

---

# Things that will go wrong (and how to fix)

## "Render deploy failed"
- Check the build logs — usually a missing env var or Python version mismatch
- Make sure `requirements.txt` exists in the root you set (`saas/control_plane`)

## "Vercel build failed"
- Look at the build output — usually a TypeScript error or missing dependency
- Run `npm run build` locally first to see the same error

## "Cannot connect to Supabase from Render"
- Make sure `DATABASE_URL` in Render env vars matches exactly what Supabase showed
- Supabase free tier pauses after 1 week of inactivity — go to the Supabase dashboard to wake it up

## "MT5 disconnects every few hours on VPS"
- Add MT5 to Windows startup folder
- Use Task Scheduler to restart the bot if it crashes

## "Stripe webhook returns 401"
- Wrong `STRIPE_WEBHOOK_SECRET` — re-copy from Stripe dashboard

---

# What I do next when you're ready

When you've completed parts 1-3 and the live stack is working, **send me your Vercel URL**. I'll:

1. Test the signup flow live
2. Spot-check the SQL data
3. Verify security headers are set right
4. Walk you through Part 4 (Stripe) when you have your KYC done

When the SaaS is live with real test users, we move to Phase 2 (automated provisioning, refactor bot to per-user, etc.).

---

# Cost summary (after this walkthrough)

| Service | Free tier limit | Cost when exceeded |
|---------|----------------|--------------------|
| Vercel | 100GB bandwidth | $20/mo Pro |
| Supabase | 500MB DB, 2GB bandwidth | $25/mo Pro |
| Render | 750 instance hours | $7/mo per service |
| Cloudflare (later) | Unlimited | $0 |
| Domain | — | ~$15/year |
| Contabo VPS (per user) | — | ~$5/mo each |
| Stripe | — | 2.9% + ₹3 per transaction |

**For your first 0-10 users: ~$5-20/month total.**
**For 50 users: ~$300/month** (mostly VPS costs at $5/user).

These costs scale linearly with your revenue (one of the reasons the IB rebate is so valuable — it scales the same way).
