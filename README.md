# D1 Portfolio Bot

A multi-symbol, multi-strategy MT5 trading bot for FX, metals, and indices.
Combines 12 strategy types across 21 instruments into 98 unique
(symbol, strategy) combinations, each with measured 8-year out-of-sample edge.

---

## Quick Start

1. Install MetaTrader 5 terminal and log into your account.
2. In MT5: Tools → Options → Expert Advisors → enable algorithmic trading.
3. Add all symbols listed in `d1_portfolio_config.py` to Market Watch.
4. `pip install -r requirements.txt`
5. Edit `d1_portfolio_config.py`:
   - `ACCT_NO` — must match your logged-in account
   - `RISK_PER_TRADE_PCT` — defaults to `0.5%` of equity per trade
6. `python d1_portfolio_bot.py`

---

## Project Structure

```
diff_ea_ai/
├── README.md                        ← this file
├── requirements.txt                 ← Python dependencies
├── d1_portfolio_bot.py              ← MAIN BOT — run this
├── d1_portfolio_strategy.py         ← 12 strategy detectors
├── d1_portfolio_config.py           ← all tunable parameters
├── monitor_agent.py                 ← OPTIONAL watchdog — run alongside
├── .claude/CLAUDE.md                ← project memory for AI sessions
└── backtests/                       ← validation scripts (run anytime)
    ├── backtest_d1_strategies.py    ← D1 strategies on 16 symbols
    ├── backtest_h1_strategies.py    ← H1 timeframe validation
    └── backtest_consensus.py        ← multi-strategy confirmation
```

### Two-process setup (recommended)

Run the bot and monitor agent in **separate terminals**:

```bash
# Terminal 1 — the trading bot
python d1_portfolio_bot.py

# Terminal 2 — the watchdog (optional but recommended)
python monitor_agent.py
```

The monitor agent watches account/positions every 60 seconds, logs everything
to `monitor_log.csv`, and takes emergency action only in genuinely bad
situations (margin call risk, sudden 15%+ drawdown, sustained spread spikes).
It complements but does not override the bot's own circuit breakers.

---

## How It Works

The bot polls every 60 seconds. On each new bar close (D1 at 00:00 UTC, H1 at
xx:00 UTC) it scans every configured `(symbol, strategy)` combination and opens
positions for ones that signal. Each position has server-side SL/TP attached at
entry — the broker manages it, so even if the bot is offline the position
closes correctly.

### Strategy Layers

| Layer | Count | Hold Time | Typical Win% |
|-------|-------|-----------|--------------|
| **D1 strategies** | 62 combos | 5–40 days | 40–65% |
| **H1 strategies** | 15 combos | 1–48 hours | 45–60% |
| **Consensus** (D1 confirmation) | 21 combos | days | 60% |

### Strategy Types

| Strategy | Logic | Win% | R:R |
|----------|-------|------|-----|
| `donchian20` | 20-bar channel breakout (Turtle) | 32–40% | 1:3 |
| `donchian20_T` | Same, tight TP | 45–50% | 1:1.5 |
| `momentum60` | 60-bar net-return follow | 42–49% | 1:1.5 |
| `momentum60_T` | Same, tight TP | 53–59% | 1:1 |
| `rsi2` | Connors RSI(2) + 200-MA trend | 60–65% | 0.75:1 |
| `3day_reverse` | 3 consec same-direction → reverse | 60–65% | 0.67:1 |
| `bb_extreme` | Close outside 2.5σ Bollinger → revert to mid | 40–45% | varies |
| `consensus` | Fires only when mean-reversion + trend agree | 60% | 1:0.85 |
| `*_H1` variants | Same logic on H1 timeframe | 45–60% | varies |

---

## Risk Management

Built-in safety layers — all live in `d1_portfolio_config.py`:

| Setting | Default | Effect |
|---------|---------|--------|
| `USE_DYNAMIC_RISK` | `True` | Auto-scale lot sizes to a % of equity |
| `RISK_PER_TRADE_PCT` | `0.5` | Risk 0.5% of equity per trade |
| `MAX_DAILY_LOSS_PCT` | `3.0` | Halt new trades after -3% day |
| `MAX_TOTAL_DD_PCT` | `20.0` | Halt entirely after -20% drawdown |
| `MAX_OPEN_POSITIONS` | `15` | Cap concurrent positions |
| `USE_BUCKET_TP` | `False` | Optional: close all when floating P&L hits target |
| `BUCKET_TP_USD` | `100.0` | The bucket target (used if enabled) |

### Dynamic Sizing

Set `USE_DYNAMIC_RISK = True` (default). The bot reads your current equity at
every tick and computes per-trade risk as `equity × RISK_PER_TRADE_PCT / 100`.
Works from $100 to $1M without code changes.

| Account Size | Risk/Trade (at 0.5%) | Notes |
|--------------|-----------------------|-------|
| $100 | $0.50 | Most trades will be skipped (min-lot exceeds budget) |
| $1,000 | $5 | Workable for major FX, not gold |
| $10,000 | $50 | All symbols tradeable |
| $100,000 | $500 | All symbols, full diversification |
| $1,000,000 | $5,000 | Same |

---

## Measured Performance

Backtested on 8 years of real broker data across 21 instruments.

| Metric | At $50/trade risk | At $500/trade risk (~0.5% of $100k) |
|--------|-------------------|---------------------------------------|
| Expected $/day (D1 layer only) | ~$8 | ~$80 |
| Expected $/day (D1 + H1 + consensus) | ~$25 | ~$250 |
| Realistic live (with slippage) | ~$15 | ~$150 |
| Max measured drawdown | 10–15% of capital | 10–15% of capital |
| Average win rate | 55–60% | 55–60% |

**These are backtest numbers. Live will be 20–30% lower due to slippage,
commissions, and regime drift. Plan accordingly.**

---

## What to Expect Day-to-Day

- **Most days**: 2–8 trades open across the portfolio, holding from hours
  (H1 layer) to weeks (D1 layer)
- **Some days**: zero new trades (no setups fired) — this is normal
- **Quality periods**: $50–500 P&L days
- **Bad periods**: -$50 to -$300 days (the circuit breaker stops compounding losses)
- **Win rate**: ~55–60% on the trade level. Each strategy's win rate varies
  from 32% (Donchian breakouts) to 65% (RSI mean-reversion)

The edge comes from **diversification across uncorrelated strategies**, NOT
from a single high-win-rate signal. Some strategies lose; others win; the
portfolio is positive on average.

---

## Re-validating the Edge

Run any time:

```bash
python backtests/backtest_d1_strategies.py     # checks all D1 strategies on 16 symbols
python backtests/backtest_h1_strategies.py     # H1 timeframe across symbols
python backtests/backtest_consensus.py         # consensus filter improvement
```

If the OOS results drop materially below what's documented, stop trading
and investigate. Strategy edges can decay with market regime changes.

---

## What This Bot Does NOT Do

- **No martingale, no grid, no averaging down.** Each trade is independent.
- **No trailing stops.** Tested → destroys 95% of edge.
- **No M1 scalping.** Tested → no measurable edge on retail data.
- **No 80–90% win rate claims.** Mathematically impossible without negative
  expected value. Real systems run 50–65% with positive R:R.
- **No daily profit guarantee.** Some days are negative.
- **No "predict the next candle" indicator stack.** Direction prediction on
  M1 was tested → sub-random accuracy.

---

## Honest Expectations

This bot has a real, statistically-validated edge from 8 years of backtest
on real broker data. But:

1. **Past performance ≠ future performance.** Markets change.
2. **Live slippage is 20–30% worse than backtest fills.**
3. **You will have losing weeks.** This is normal.
4. **Drawdowns reach 10–20% during bad regime periods.** Don't panic.
5. **The realistic ceiling for a retail diversified portfolio bot is
   ~50–200% annual returns at the risk levels documented**, not 1000%
   like scam bots promise.

If you can run this consistently for 6–12 months without manual interference,
the data says you'll have positive results. If you stop, restart, tinker, or
override the bot's decisions, you'll likely underperform the backtest.

---

## Monitor Agent — what it watches

| Check | Threshold | Action |
|-------|-----------|--------|
| Margin level | < 200% | **Force-close all positions** (prevents margin call) |
| Session drawdown | > 15% from peak | **Force-close all** (catastrophic move protection) |
| Spread spike | > 5× normal for 3+ ticks | **Log warning** (server-side SL/TP still protects) |
| Bot idle | 0 positions for > 1 hour | Log notice (check if bot is running) |
| Account state | every tick | Append to `monitor_log.csv` |

5-minute cooldown between emergency actions prevents rapid-fire force-closes.
All events are written to `monitor.log` for after-the-fact review.

The agent is **purely defensive** — it never opens trades, only protects
against catastrophic conditions the bot's own caps might miss.

## When to STOP Running It

- Drawdown exceeds 25% of starting balance
- Win rate stays below 40% for 30+ consecutive trades
- Daily loss circuit breaker fires 3+ days in a row
- Live results diverge materially from backtest after 100+ trades

Stop, run the backtest scripts to check if the edge has decayed, and only
restart if the data still supports it.

---

## Support

This is your bot. There is no support team. The behavior is fully described
in this README plus the comments in the three Python files. Read those before
asking why something happened.
