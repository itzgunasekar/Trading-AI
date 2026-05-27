# VPS Setup For Your Trading Bot (Step 0 of validation)

**This is the simple, do-it-right-now guide.** Moves your bot from your laptop to a cloud VPS so it runs 24/5 during validation.

⚠ **This is for YOUR OWN bot during validation.** The full SaaS deployment (for paying customers) comes later in `DEPLOYMENT_WALKTHROUGH.md`. Don't confuse the two.

**What you're doing today:** rent a small cloud Windows computer, install MT5 + Python + the bot there, leave it running.

- **Cost:** ~$5-6/month
- **Time:** ~45 minutes
- **Result:** Bot trades 24/5 even if your laptop is off

---

## Step 1 — Buy a Windows VPS (10 min)

A VPS = a small Windows computer in the cloud that never turns off.

1. Open browser → **`https://contabo.com/en/vps/`**
2. Click **"Configure"** under **"VPS S"** (cheapest, plenty for the bot)
3. On the next page, choose:
   - **Region:** pick the one closest to you (Asia/Singapore if available)
   - **Operating System:** scroll to **Windows** → pick **Windows Server 2022**
   - **Storage:** leave default
   - **Contract period:** pick **1 month** (don't commit longer until you've tried it)
4. Click **Continue** → sign up → pay (~$5-6 first month, includes Windows license)
5. **Wait 15-30 minutes.** They'll email you:
   - **IP address** (looks like `194.xxx.xxx.xxx`)
   - **Username** (usually `Administrator`)
   - **Password**

**Save these 3 things in a safe place** (Notepad, password manager).

---

## Step 2 — Connect to your VPS (5 min)

You'll see the cloud computer's screen on your own laptop.

1. On your laptop, press **`Windows key + R`**
2. Type: `mstsc` → press Enter
3. **Computer:** type the IP from Contabo's email
4. Click **Connect**
5. Username + password from the email
6. If asked about certificate → click **Yes**

You're now looking at the cloud computer's desktop. Anything you do here happens on the cloud, not your laptop.

---

## Step 3 — Install MT5 on the VPS (10 min)

1. Inside the VPS desktop, open **Edge browser**
2. Go to your broker's site → download **MetaTrader 5**
3. Install (Next, Next, Finish)
4. Open MT5 → **File → Login to Trade Account**
5. Enter your SAME MT5 account number + password + server (the ones from your laptop)
6. Click **Login** — you should see your account balance and any existing positions
7. Top toolbar → click **Algo Trading** so it turns green/active

---

## Step 4 — Install Python on the VPS (5 min)

1. Inside the VPS, in Edge browser, go to **`python.org/downloads/`**
2. Click the big yellow **Download Python 3.x** button
3. Run the installer
4. ⚠ **CRITICAL:** at the bottom of the installer, **CHECK "Add python.exe to PATH"**
5. Click **Install Now** → wait → **Close**

---

## Step 5 — Copy bot files from laptop to VPS (5 min)

Easiest method: copy-paste via Remote Desktop clipboard.

1. On your **laptop**: File Explorer → `D:\Ajith\diff_ea_ai\`
2. Select these 9 files (Ctrl+click each):
   - `d1_portfolio_bot.py`
   - `d1_portfolio_strategy.py`
   - `d1_portfolio_config.py`
   - `trade_intelligence.py`
   - `strategy_health.py`
   - `monitor_agent.py`
   - `analytics_agent.py`
   - `process_lock.py`
   - `run_all.py`
3. **Ctrl + C** to copy
4. Switch to the VPS Remote Desktop window
5. On VPS desktop, right-click → **New → Folder** → name it `bot`
6. Double-click the new `bot` folder to open it
7. **Ctrl + V** to paste

Wait ~1 minute for the files to copy over the network.

---

## Step 6 — Install bot dependencies (3 min)

On the VPS:

1. Click **Start menu** → type **`powershell`** → press Enter
2. In the PowerShell window, type each line and press Enter:

```
cd C:\Users\Administrator\Desktop\bot
```

*(If you put the bot folder somewhere else, replace the path. Easy trick: in File Explorer, right-click the `bot` folder → "Copy as path" → in PowerShell type `cd ` (with space) then Ctrl+V paste.)*

```
pip install MetaTrader5 numpy
```

Wait ~1 minute. The end should say `Successfully installed MetaTrader5-... numpy-...`.

---

## Step 7 — Start the bot (1 min)

Same PowerShell window:

```
python run_all.py
```

You should see something like:

```
======================================================================
Unified Launcher — D1 Portfolio Trading System
======================================================================
  starting BOT -> d1_portfolio_bot.py
[BOT] >>> d1_portfolio_bot starting...
[BOT] [lock] acquired d1_portfolio_bot.pid
[BOT] Account: <your account>  Equity: $...
[BOT] Active combinations: 98  risk_per_trade=...
```

🎉 **The bot is now running on the cloud.**

---

## Step 8 — Make it auto-restart (5 min)

So the bot starts again if the VPS reboots (Windows updates, etc.).

1. On the VPS, press **`Windows key + R`** → type `shell:startup` → Enter
   - A folder opens
2. Right-click inside the folder → **New → Shortcut**
3. Paste this in the location field (adjust path if needed):

```
powershell.exe -NoExit -Command "cd C:\Users\Administrator\Desktop\bot; python run_all.py"
```

4. **Next** → name it `D1 Bot` → **Finish**

Done. Bot auto-starts on every reboot.

---

## Step 9 — Disconnect (the bot KEEPS running)

This is the important part — most beginners get this wrong.

1. On the VPS, **just close the Remote Desktop window** (the red ✕ at the top)
2. ⚠ **DO NOT click "Sign out" in the VPS Start menu** — that logs you out and stops the bot
3. Just close the window

The VPS continues running in the cloud. You can shut your laptop. The bot keeps trading.

To check on it later:
1. `Win + R` → `mstsc` → Enter
2. Connect to your VPS IP
3. The PowerShell window with bot logs should still be running
4. Look at the logs to see what trades have happened since last time

---

## Quick check — did it work?

After ~5 minutes:

✅ VPS Remote Desktop shows MT5 logged in (your account number visible top-left of MT5)
✅ PowerShell window shows `[BOT]`, `[MON]`, `[AN]` log lines
✅ Your MT5 (on either VPS or laptop — same account) shows positions

If all 3 = ✅ → done. **Bot is in the cloud.**

You can now stop running the bot on your laptop (close the run_all.py terminal there).

---

## If something goes wrong

Tell Claude the step number and what you see on screen. Don't try to figure it out alone.

Common issues:

| Problem | Fix |
|---------|-----|
| Contabo email never came after 1 hour | Check spam folder. If still missing, contact Contabo support. |
| `mstsc` won't connect to the VPS IP | Wait 30 more min — VPS still booting. |
| "Algo Trading" button stays grey | Tools → Options → Expert Advisors → check "Allow algorithmic trading" |
| `pip install` says "command not found" | Python wasn't added to PATH. Reinstall, CHECK that box. |
| Bot complains "MT5 init failed" | MT5 isn't open / logged in. Open it manually on the VPS first. |

---

## What's next

✅ **Tell Claude: "VPS done, bot running"**

Then we:
1. Watch the bot for 2-3 days to confirm it's stable on the VPS
2. Continue with `PRE_LAUNCH_CHECKLIST.md` Steps 1-4 in parallel (IB account, Razorpay, lawyer, validation tracking)
3. Once Step 4 (8-week validation) passes → move to `DEPLOYMENT_WALKTHROUGH.md` for the SaaS webapp

For today, just focus on getting Steps 1-9 above done. **One step at a time.**
