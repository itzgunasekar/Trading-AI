# Operations Guide — Day-to-Day Running

Concise reference for operating the D1 Portfolio Bot in production.

---

## Starting the bot

```bash
python run_all.py
```

This launches three processes in one terminal:
- **BOT**  — `d1_portfolio_bot.py` (signal generation + trade execution)
- **MON**  — `monitor_agent.py` (risk watchdog, emergency closures)
- **AN**   — `analytics_agent.py` (live performance metrics)

All output is colour-tagged and mirrored to `logs/{bot,monitor,analytics}.log`.

---

## Restarting after a code change

**`run_all.py` does NOT auto-kill old bots.** If you re-run it while the previous
session is still alive, the new processes will detect the existing `.pid`
files via `process_lock.acquire_or_die()` and exit immediately. This is a
safety feature — it prevents two bots opening duplicate trades.

**Correct restart sequence:**

1. Go to the terminal currently running `run_all.py`
2. Press **Ctrl+C** (sends SIGINT → cleanly terminates all 3 child processes)
3. Wait until you see `>>> all stopped` (≤ 5 seconds)
4. Start fresh: `python run_all.py`

The new code (config, strategy, rescue layer, anything) is now live.

---

## Stopping cleanly

Same as restart step 1–3 above. The bot saves state on shutdown:
- `strategy_health.json` — rolling health counters
- `strategy_cooldown.json` — agent-imposed cooldowns
- `peak_equity.json` — shared drawdown baseline
- `mfe_mae.json` — per-position MFE/MAE tracker

These are read again on next launch — your trades, health, and rescue state
survive restarts.

---

## What happens if the bot crashes?

- **Open positions** stay open at the broker with their server-side SL/TP
  (broker enforces — no daemon needed for trade-level safety).
- **Bot state** is persisted to the JSON files above and reloaded on
  restart. The monitor agent's `.pid` becomes stale and is auto-cleaned by
  `process_lock` next time you start.
- **PID files left behind** are tolerated — `process_lock` checks whether
  the PID is actually alive before refusing to start.

If the bot has been down for a while, on restart it adopts existing open
positions by MAGIC range + COMMENT match.

---

## Watching live activity

```bash
tail -f logs/bot.log         # signal generation + bucket fires
tail -f logs/monitor.log     # equity / margin / agent decisions
tail -f logs/analytics.log   # rolling P&L stats
```

Key log lines to recognise:

| Line | Meaning |
|---|---|
| `>>> XAUUSD ... BUY 0.02 @ 4540.5 ...` | New position opened |
| `>>> SMART BUCKET CLOSE: 5/5 positions ...` | Bucket-TP fired |
| `[rescue-keep] #ticket ...` | Rescue layer kept a near-SL loser |
| `[health] DEACTIVATED sym strat` | Auto-deactivation due to decay |
| `[health] REACTIVATED sym strat` | Paper-recovery threshold met |
| `[CORRELATION GUARD: ...]` | Agent same-currency exposure cap |
| `agent-close #ticket pnl=...` | Agent force-closed a position |

---

## Grading the Loser Rescue Layer

After ~2 weeks of live decisions:

```bash
python analytics/grade_rescue.py
```

This joins `logs/rescue_decisions.csv` against MT5 history and reports
per-ticket outcomes. Use it to validate the rescue threshold over time.

## Grading SL Migration (BE move + age-decay TP-tighten)

The bot moves SL to entry+small-buffer when a position reaches 50% of its
TP distance. This protects capital but can cause BE-stops on pullbacks.
To decide whether to keep / push trigger / disable, run:

```bash
python analytics/grade_sl_migration.py
```

This joins `logs/sl_migration_events.csv` (written by `trade_intelligence.
migrate_position_stops()`) with MT5 deal history and reports:

- **TP-hit rate** — % of BE-migrated positions that closed at full TP
- **BE-stop rate** — % that closed near entry+buffer (BE protection used)
- **Mid-exit rate** — % that closed between BE and TP (e.g. age-decay TP
   tighten, bucket sweep, agent close)
- **Reversal-loss rate** — % that closed past the new SL (rare, usually
   agent emergency close or wide gap)
- **Per-strategy breakdown**
- **Recommendation** — keep at 0.50, raise to 0.65/0.75, or disable

Decision thresholds the script uses (you can re-tune them in the script):

| BE-stop rate | What it means | Recommendation |
|---|---|---|
| < 25% | BE-stops rare; protection earns its keep | KEEP `SL_MIGRATION_TRIGGER = 0.50` |
| 25-45% | Moderate cost; BE-stops are eating some R | RAISE to 0.65-0.75 |
| > 45% | Too many stop-outs near BE | RAISE to 0.80+ or DISABLE |

Needs ≥20 graded events before a verdict — let the bot run ~2-4 weeks.

### How to apply the recommendation

If the grader says "raise to 0.65" or similar, edit
[d1_portfolio_config.py](../d1_portfolio_config.py):

```python
SL_MIGRATION_TRIGGER     = 0.65    # was 0.50
```

Then restart (Ctrl+C the run_all.py terminal → `python run_all.py`).
Note: existing open positions keep their current SL — only NEW positions
will use the new trigger.

### Counterfactual caveat

The grader's "estimated cost of BE protection" line uses a 35% recovery
assumption (typical for trend strategies — how often a stopped-out trade
would have gone on to TP if SL hadn't been touched). This is an estimate;
the true value is unknowable since closing the position prevents observing
what would have happened.

---

## Re-running backtests

```bash
python backtests/backtest_rescue_recovery.py            # D1 rescue
python backtests/backtest_h1_rescue_recovery.py         # H1 rescue
python backtests/backtest_variants_rescue_recovery.py   # _T + consensus
python backtests/oos_validate_rescue.py                 # 70/30 OOS check
python backtests/sweep_hard_close.py                    # HARD_CLOSE knob sweep
```

The original reference backtests are preserved for historical comparison:

```bash
python backtests/backtest_d1_strategies.py     # v9 reference
python backtests/backtest_h1_strategies.py     # v12 reference
python backtests/backtest_consensus.py         # consensus reference
```

---

## State files cheat sheet

| File | Purpose | Safe to delete? |
|---|---|---|
| `strategy_health.json` | Rolling per-combo health + recovery counters | NO — lose all live calibration |
| `strategy_cooldown.json` | Agent/bot cooldown blocks | OK if no positions open |
| `peak_equity.json` | Shared drawdown baseline | OK — reset means fresh peak from current equity |
| `mfe_mae.json` | Per-position MFE/MAE tracker | OK if no positions open |
| `risk_multiplier.txt` | Agent → bot risk scaling flag | OK — default is 1.0× |
| `news_blackout.flag` | Agent → bot news-window flag | OK — auto-rewritten |
| `*.pid` | Process locks | OK while bots are stopped |
| `logs/*.log` | Diagnostic logs | OK but you lose audit trail |

---

## Health checks

| Symptom | What to check |
|---|---|
| Bot opens no trades for hours | `logs/bot.log` for `[quality-filter]` rejections, `[health]` deactivations, or `[off-market]` cooldowns |
| Many `agent-close` events | `logs/monitor.log` for the reason tag (`stale`, `correlation`, `emrg-margin`, `emrg-drawdown`) |
| Bucket fires immediately at startup | Stale bar tracker — verify `[startup] primed N keys` message in bot.log |
| Drawdown halt at unexpected level | Check `peak_equity.json` — both bot and agent share it now |
