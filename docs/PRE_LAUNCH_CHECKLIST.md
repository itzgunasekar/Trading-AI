# Pre-Launch Checklist — Validate BEFORE You Build

**Do these 5 things in order. They cost almost nothing and save you from building infrastructure for a product that doesn't actually work.**

Estimated time: ~6-8 weeks (because step 5 needs market time, not your time).
Estimated cost: $5/month for step 0, $0 for steps 1-2, $200-400 for step 3, $0 for step 4 (your bot is already coded).

---

## ✅ Step 0: Move your bot to a $5/month VPS (so validation runs 24/5)

**Why first?** Your laptop sleeps. The internet drops. Windows updates restart. None of that can happen during the 6-8 week validation — the bot must run continuously. A tiny cloud VPS solves it.

**Time: ~45 minutes**

This is the SAME VPS pattern you'll later use for each paying customer in production. By doing it now for yourself, you're learning the operational skill that becomes your Phase 1 onboarding flow.

The detailed click-by-click instructions are in **`VPS_SETUP_FOR_YOUR_BOT.md`** (separate doc — easier to follow).

After completing Step 0, you can shut your laptop and the bot keeps trading. Then while it validates over the next 6-8 weeks, you do Steps 1-3 below in parallel.

**Status:** ☐ Contabo VPS bought ☐ MT5 + Python installed ☐ Bot files copied ☐ Bot running ☐ Auto-restart on reboot configured

---

## ✅ Step 1: Open IC Markets IB (Introducing Broker) account

**Why this first?** This is your "free money" revenue stream. The broker pays you $3-5 per lot every user trades. No user-side fees needed. Without this, your business model becomes 100% dependent on charging fees, which is harder to sell.

**Time: 15 minutes to apply, 1-3 business days to be approved**.

### Action items

1. **Open this URL in your browser:**
   ```
   https://www.icmarkets.com/global/en/partners/introducing-broker
   ```

2. **Click "Become an Introducing Broker"** (usually a button at the bottom of the page)

3. **Fill in the application form:**
   - Personal details: your full legal name, email, phone, country (India)
   - Type of business: pick "Individual / Sole Trader" for now (you can upgrade to company later)
   - Source of clients: write something like *"Operating a quantitative trading algorithm SaaS — referring beta users to brokers with reliable MT5 platforms."*
   - Expected monthly volume: be honest, write "10-50 lots/month initially, scaling with user growth"
   - Website: if you have a domain, put it. If not, write "in development"

4. **Submit.** You'll get a confirmation email within minutes.

5. **Within 1-3 business days, an IB manager emails you** asking for:
   - ID proof (passport or PAN card + Aadhaar)
   - A brief description of how you'll refer clients
   - Sometimes a video call

6. **Once approved, they give you:**
   - A unique referral link (the URL you'll use in the SaaS)
   - A login to their IB Portal (shows your earnings per user)
   - The rebate structure in writing (typically $3-5 per lot)

### What to do with the referral link

**Save it!** You'll put it in your webapp's signup flow so every user who signs up via your link is forever attributed to you. The broker pays you for the lifetime of that user's trading.

### Common questions answered

| Question | Answer |
|----------|--------|
| Do I need a company? | No, individual is fine initially. Upgrade to a Pvt Ltd later if you scale. |
| Do they pay in INR or USD? | USD, by wire transfer to your bank account. ~$25 fee on incoming wires from foreign brokers. |
| Tax implications? | Income tax applies in India. ~30% bracket once you cross thresholds. Talk to a CA. |
| Can I IB multiple brokers? | Yes — open IC Markets first, then Pepperstone, FP Markets etc. as Plan B. |

**Status:** ☐ Started ☐ Application submitted ☐ Approved ☐ Referral link saved

---

## ✅ Step 2: Open a Stripe account

**Why?** This is how you'll collect daily performance fees from users. Cards work globally, recurring debit just works, dispute protection is built-in.

**Time: 30-60 minutes to apply, 1-7 days to be activated.**

### Two paths depending on your situation

#### Path A — You have an Indian-registered business
- Apply at: `https://dashboard.stripe.com/register`
- Country: India
- Business type: "Sole Proprietorship" if you don't have a Pvt Ltd
- You'll need: PAN, GST (if turnover > ₹20L), business bank account
- **Indian Stripe only supports INR right now**, so you'd charge users in INR
- Indian Stripe also requires you to have a CIN (company) for collecting subscription payments from cards in some cases

#### Path B — Want to charge users in USD (recommended for SaaS)
- Use **Stripe Atlas** instead: `https://stripe.com/atlas`
- Stripe Atlas creates a Delaware C-Corp for you (~$500 one-time fee)
- Then you can collect USD globally, withdraw to a US bank account (Mercury or Wise)
- This is the path most Indian SaaS founders take for international customers
- Tax handling is more complex — you'll need an Indian CA + US tax filing (Form 5472)
- **Recommendation**: do Atlas only AFTER you've validated the product. Initially use Path A in INR.

#### Path C — UPI / Razorpay (Indian users only)
- If your users will be Indian: skip Stripe, use Razorpay
- `https://razorpay.com/payments/`
- Easier KYC, accepts UPI Mandate for recurring debit
- Same code shape as Stripe — we'd just swap the SDK

**For MVP I recommend: Razorpay** if your first users are Indian friends/family, **Stripe Atlas** if you want global ambition from day 1.

### Action items (Razorpay path — easiest for MVP)

1. **Open:** `https://dashboard.razorpay.com/signup`
2. **Sign up with your email + phone**
3. **Complete KYC:**
   - PAN of business (your personal PAN if proprietor)
   - Bank account details
   - Aadhaar
   - Selfie video
4. **Wait 1-2 days for activation**
5. **Once activated, go to Settings → API Keys** and save:
   - `Key ID` (starts with `rzp_test_...` or `rzp_live_...`)
   - `Key Secret`
6. **Save both keys** somewhere safe (1Password, BitWarden, encrypted notes app)

**Status:** ☐ Started ☐ KYC submitted ☐ Activated ☐ API keys saved

---

## ✅ Step 3: Talk to a fintech lawyer (~$200-400, 30-60 min consultation)

**Why?** Operating a "I trade on your account for a fee" service in India is regulated by SEBI. You could need an Investment Advisor (IA) registration. Without one, **you may be operating illegally** and can be fined or shut down.

**This is the single biggest risk** to your project. Don't skip it.

### How to find one

Search for "SEBI registered fintech lawyer in [your city]" or use:
- **LawRato.com** — filter by "Securities Law" or "SEBI" 
- **LegalKart** — similar marketplace
- **LinkedIn**: search "fintech lawyer India" + your city
- **Vakilsearch** — they have packages

Get **3 quotes**. Pick the one who:
- Has actual SEBI experience (ask for case examples)
- Speaks plainly about risks
- Charges per hour, not a flat package (you want flexibility)

### Questions to ask in the 30-min call

Write these down and ask one by one:

1. *"I'm building a SaaS where users let my algorithm trade their broker account. I take a % of profits. Do I need SEBI registration?"*
2. *"My users sign up for an offshore broker (IC Markets — Seychelles regulated). Does that change the answer?"*
3. *"I'm planning to operate as 'beta access only' for the first year. Does that limit the regulatory exposure?"*
4. *"What's the minimum legal structure I need — sole proprietor, LLP, Pvt Ltd?"*
5. *"Should I make users sign an Investment Advisory Disclaimer or a different document?"*
6. *"What's the legal risk if a user loses money and sues me? What protects me?"*
7. *"Do I need to register the company in India, or is Mauritius/BVI a better path?"*
8. *"What KYC do I need to collect from my own users?"*
9. *"If a user's broker account gets hacked (not my fault), what's my liability?"*
10. *"Can you draft me a Terms of Service + Risk Disclosure document? What would that cost?"*

### Outcomes to get from the call

By the end, you should have written notes covering:

- [ ] **Whether you need a license** to operate in India (yes/no/maybe with explanation)
- [ ] **What legal entity** to use (sole prop / Pvt Ltd / offshore)
- [ ] **A draft ToS + Risk Disclosure** OR a quote to have them draft one (~₹20-50k typically)
- [ ] **What to do BEFORE you take real users** (registration, KYC, etc.)

### If the lawyer says "you need a SEBI Investment Advisor license"

Two options:

**Option 1: Get the license** — takes ~6 months and ~₹2-5L in fees + ongoing compliance. Not unreasonable if you're serious.

**Option 2: Pivot to "signal service"** — instead of YOU trading on user's account, you just SEND them buy/sell signals via Telegram/email. They execute manually or through their own EA. This is much less regulated. But it reduces conversion rate dramatically.

**Option 3: Offshore entity** — register the operating company in Mauritius / BVI / Vanuatu. Adds complexity but sidesteps SEBI. Common for crypto/forex services. ~₹50-150k setup + ongoing fees.

The lawyer will help you choose. **Don't pre-decide without their input.**

**Status:** ☐ Found lawyer ☐ Booked call ☐ Call done ☐ Decision documented

---

## ✅ Step 4: Run the bot on YOUR OWN money for 4-8 weeks

**Why?** Until you prove the bot actually makes money in live (not backtest), you have nothing to sell. This is the most important step.

**Time: 4-8 weeks of market activity. Your active time per week: 30 minutes to review.**

### Setup

You're already doing this — the bot at `D:\Ajith\diff_ea_ai\d1_portfolio_bot.py` is running on account `107185456`. Keep it running.

### What to track every week

Open `D:\Ajith\diff_ea_ai\analytics\report.txt` once per week (it refreshes every 30 minutes). Record these numbers in a simple spreadsheet:

| Week | Starting equity | Ending equity | $ change | % change | # trades | Win rate | Best day | Worst day | Notes |
|------|----------------|---------------|----------|----------|----------|----------|----------|-----------|-------|
| 1 | $100,000 | ? | ? | ? | ? | ? | ? | ? | ? |
| 2 | ? | ? | ? | ? | ? | ? | ? | ? | ? |
| 3 | ? | ? | ? | ? | ? | ? | ? | ? | ? |
| 4 | ? | ? | ? | ? | ? | ? | ? | ? | ? |
| 5 | ? | ? | ? | ? | ? | ? | ? | ? | ? |
| 6 | ? | ? | ? | ? | ? | ? | ? | ? | ? |
| 7 | ? | ? | ? | ? | ? | ? | ? | ? | ? |
| 8 | ? | ? | ? | ? | ? | ? | ? | ? | ? |

### GO / NO-GO criteria at week 8

**GO** (proceed to deployment) if **ALL** of these are true:
- [ ] 8-week return is **positive** (any positive number — even +1%)
- [ ] No week had a loss bigger than **5% of equity**
- [ ] Win rate stayed in the **45-65% range**
- [ ] Bot crashed less than 3 times AND recovered each time
- [ ] No "edge_health: BELOW_BACKTEST" warnings persistent for more than 2 weeks

**NO-GO** (pause the SaaS plan, fix issues first) if **ANY** of these:
- 8-week return is negative
- A single week lost more than 10%
- Bot crashed and DID NOT recover (positions left unprotected)
- More than 30% of trades closed via "stale-cleanup" or "agent-close" (means strategy isn't working as designed)
- You found yourself manually overriding the bot more than once

### What if NO-GO?

Don't panic — this is exactly what validation is for. Options:
1. **Reduce risk per trade** to 0.25% and run another 4 weeks
2. **Drop the worst-performing strategies** (analytics report shows which)
3. **Try only D1 strategies** (disable H1 ones) — more conservative
4. **Stop and re-evaluate** — maybe the SaaS idea needs a different bot

It's much better to discover this on YOUR $100k account than after taking 20 paying customers.

### Useful daily-ish habit (5 min)

Each morning IST (before market opens in NY):
1. Open MT5 — check account balance is reasonable
2. Open `analytics/report.txt` — scan for any "BELOW_BACKTEST" warnings
3. Open `monitor.log` — check for any `[ALERT]` lines from the last 24h
4. If anything looks off, take a screenshot and write the date down

**Status:** ☐ Week 1 ☐ Week 2 ☐ Week 3 ☐ Week 4 ☐ Week 5 ☐ Week 6 ☐ Week 7 ☐ Week 8 ☐ GO/NO-GO decision made

---

## When all 4 steps are ✅

You'll have:
1. A working revenue stream (IC Markets IB)
2. A working payment processor (Razorpay/Stripe)
3. A legal path that you understand
4. Real-world proof your bot makes money

**That's the foundation.** With that in hand, the deployment of `saas/` becomes a low-risk technical exercise rather than a leap of faith.

Then you do Option A (deployment walkthrough) which I'll prepare next.

## Cost summary

| Item | Cost |
|------|------|
| IC Markets IB application | Free |
| Razorpay activation | Free |
| Fintech lawyer call | ₹15,000-30,000 |
| ToS + Risk Disclosure drafting | ₹20,000-50,000 (optional, can DIY initially) |
| 4-8 weeks of live trading | Free (already running) |
| **Total** | **₹15,000-80,000** |

## Honest timing reality

| Activity | Calendar time |
|----------|---------------|
| Step 1 — IB application | 1-3 days |
| Step 2 — Stripe/Razorpay | 1-7 days (in parallel with step 1) |
| Step 3 — Lawyer call | 1-2 weeks to find + book |
| Step 4 — Validation | **4-8 weeks of market time** |
| **Total before Phase A** | **6-10 weeks** |

This feels slow. **It is the correct pace.** Founders who rush past validation almost always regret it.

While step 4 runs in the background, you do steps 1-3. By the time validation is done, the legal/admin foundation is in place.
