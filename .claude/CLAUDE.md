# Project Memory — D1 Portfolio Bot

This file documents key context for future Claude sessions working on this project.

## What this project is

A multi-symbol, multi-strategy MT5 trading bot for FX, metals, and indices.
98 (symbol, strategy) combinations across 12 strategy types and 21 instruments.
8-year out-of-sample-validated edge. Currently runs on demo account 107185456
(MetaQuotes-Demo, USD).

## What this project is NOT

- NOT a martingale, grid, or scalping bot. Those were tested and found losing.
- NOT a "90% win rate" system. That's marketing nonsense; impossible without
  negative expectancy.
- NOT something to tinker with reactively. Trade decisions are 8-year-validated.

## Architecture

```
d1_portfolio_bot.py        ← runner (polls every 60s, scans bar closes, places trades)
d1_portfolio_strategy.py   ← 12 strategy detectors as pure functions
d1_portfolio_config.py     ← all parameters
backtests/                 ← three reference backtests
```

## Critical conventions

- **Position identification by COMMENT, not just magic.** The `position_strategy()`
  function reads the comment field (stable across config changes) before falling
  back to magic-number decoding. Don't break this — past bugs came from magic
  numbers changing when ACTIVE_COMBINATIONS was reordered.

- **Server-side SL/TP always set with the order.** Never rely on bot-side
  trailing or close logic for trade-level protection. If the bot dies, positions
  must still close safely at the broker.

- **Next-bar-open entry, never intra-bar.** Look-ahead bias is the #1 trap.
  Earlier backtests (v3, v6) had look-ahead and showed inflated results.
  Backtest_v7_verify caught it; the fix was strict next-bar-open entry.

- **Use OOS validation.** Every new strategy variant must pass: positive total
  AND positive out-of-sample (last 30% of data window).

## What was tested and rejected (don't re-propose)

| Idea | Why rejected |
|------|--------------|
| M1 scalping (12 variants tested) | All lost money. predict.py signal is noise. |
| Trailing stops | Destroys 95% of edge in trend-following systems. |
| Ultra-tight D1 TPs (0.5×SL) | Costs 60-75% of edge. |
| 80-90% win rate target | Impossible without negative EV. |
| Daily 24h time-stop on D1 | Same as ultra-tight: costs 60-75%. |
| Look-ahead "1-bar entry at signal price" | Inflates results 10x; always use next-bar open. |
| Volatility regime filter | Tested, mixed results, not added by default. |
| Session/weekday filter | Tested, mixed results, not added by default. |

## What was tested and added

| Feature | Impact |
|---------|--------|
| 12 strategies × 21 symbols | 98 combinations of measured edge |
| Tight-TP variants (donchian20_T, momentum60_T) | Higher win rate, same $/day |
| H1 timeframe variants | Daily-closure trades (1-48h hold) |
| Consensus filter | +36% $/day improvement |
| Dynamic equity-% sizing | Auto-scales for $100 to $1M accounts |
| Bucket TP (opt-in) | Lock daily wins, slight EV reduction |
| State recovery + comment-based identification | Survive restart, no duplicates |
| Loser Rescue Intelligence Layer (2026-05-26) | +$40k vs old 70% bucket rule on 8-yr D1 backtest |

## Loser Rescue Layer — read before tinkering with `loser_rescue.py`

The rescue layer replaces the binary "close any loser ≥70% to SL" rule in
smart_bucket_close with a per-position recovery-probability score. The
historical backtest of this layer is in `backtests/backtest_rescue_recovery.py`
(D1), `backtest_h1_rescue_recovery.py` (H1), and
`backtest_variants_rescue_recovery.py` (_T and consensus).

Key measured findings — all verified, do NOT re-litigate without new data:

- **Recovery rate from 70%-SL touch is 9-17% across all strategies and
  timeframes.** The intuition that mean-reversion strategies recover more
  often than trend strategies is WRONG by this measure; recovery rates are
  family-agnostic in the 10-17% band.

- **But keeping near-SL losers is still strongly net positive.** Asymmetric
  R:R math: a kept trade either recovers to full TP (1.5-3R) or hits a
  slightly worse SL (-1.0R vs -0.7R). Break-even recovery for 3:1 strategies
  is ~7.5%; for 1:1 strategies ~15%. Measured 10-17% clears break-even.

- **HARD_CLOSE threshold sweep (backtests/sweep_hard_close.py) showed every
  threshold below 99% costs money.** Even at 99% adverse, recoveries pay
  enough to beat closing at -0.99R. RESCUE_HARD_CLOSE_PCT = 99 is the
  empirical optimum — DO NOT lower it without re-running the sweep.

- **Bootstrap defaults in `loser_rescue._MEASURED_RECOVERY` are real
  measurements**, not guesses. Live strategy_health.recovery_rate() replaces
  them per (sym, strat) once near_sl_touches >= RESCUE_MIN_SAMPLES (=10).

- **Backtest is an upper bound on real-life edge.** The script assumes every
  70%-touch is a rescue decision; in reality, the rescue layer only fires
  when the bucket-sweep is about to fire. Real-life edge is likely 30-60%
  of the backtest number, still net positive.

Measured edge over 8 years at $50 risk/trade:
  D1 strategies: +$22,765 (vs LEGACY) / +$20,590 (vs NATURAL)
  H1 strategies: +$131,072 (vs LEGACY) / 600+ days of data
  _T variants:    +$16,914 (vs LEGACY)
  consensus:      +$3,215 (vs LEGACY)
At live 0.5% equity risk on $100k account, ≈ +$2,700–4,500/year of edge.

## Project audit fixes (2026-05-26)

Full audit identified and corrected the following overlaps/bugs:

1. **Correlation guard winner bug** (monitor_agent.py): the agent was closing
   winning positions under "correlation" logic. Closed -$2,199 lifetime.
   Fix: filter to losers only in close_specific_positions builder; raised
   MAX_SAME_CURRENCY_POS 4→7, CORR_GUARD_LOSING_THRESH -50→-500,
   CORR_MIN_AGE_SECONDS 1800→14400.

2. **Drawdown baseline divergence** (bot vs agent): each maintained its own
   peak_equity. New `peak_equity_store.py` persists the peak in
   `peak_equity.json`, both processes read+update it. Restarts no longer
   reset the baseline.

3. **Rescue layer + BE-move conflict**: after `migrate_position_stops` pulled
   SL to break-even+buffer, the rescue's consumed_pct used the migrated SL,
   making a BE-protected winner that returned to entry compute as ~100%
   consumed and trigger hard-close. Fix: MFE/MAE tracker captures
   `original_sl` at first sight; consumed_pct always uses it.

4. **Bot bucket-close churn**: bucket sweep had no cooldown — bot could
   immediately reopen the same (sym, strat) at the next bar close.
   Fix: smart_bucket_close writes `strategy_cooldown.json` entries with
   BOT_COOLDOWN_AFTER_CLOSE_HOURS=1.0 (shorter than agent's 4h since bot
   bucket fires are routine, not emergencies).

5. **Stale cleanup closed winners**: same anti-pattern as correlation guard.
   Fix: `check_stale_positions` skips any position currently in profit.

6. **Strategy deactivation knife-edge**: flat 35% WR floor matched
   donchian20's expected WR exactly → false-positive deactivations on
   healthy donchian20 combos. Fix: per-strategy floor = 70% of
   BACKTEST_EXPECTATION, never below 20%.

Phase 2 cleanup (2026-05-26) — additional consolidation:

7. **Indicator math centralized** into `indicators.py`. Used by strategy
   module, trade_intelligence, loser_rescue, and the three rescue backtests.
   The original v9/v10/v12 reference backtests keep their copies (preserve
   historical snapshots).
8. **Monitor agent thresholds centralized** into d1_portfolio_config.py.
   Both bot and agent now import from one source of truth.
9. **Close-request handling deduplicated** into `close_helpers.py`. Both
   bot and agent use the same MT5 quirk-handling code (filling-mode
   fallback, retcode handling).
10. **Quality vs rescue threshold philosophy** documented inline in
    d1_portfolio_config.py — different cost structures, intentionally
    different bars.

Measurement infrastructure (2026-05-26):
  Two CSV decision logs feed analysis scripts. Pattern: log every decision
  event with full context, join against MT5 history later, recommend tuning.

  logs/rescue_decisions.csv     ← written by loser_rescue.log_decision()
                                  read by analytics/grade_rescue.py
  logs/sl_migration_events.csv  ← written by trade_intelligence.
                                    _log_migration_event() (NEW)
                                  read by analytics/grade_sl_migration.py
                                  Used to decide: keep SL_MIGRATION_TRIGGER=0.50,
                                  raise to 0.65/0.75, or disable USE_SL_MIGRATION.

  Both loggers wrapped in try/except — never raise, never block trading.
  Both files written single-threaded by the bot process only.

Formal 70/30 OOS validation (2026-05-26) — backtests/oos_validate_rescue.py:
  IS rescue-vs-legacy:  +$22,009
  OOS rescue-vs-legacy: +$16,624  (76% above the +$9,432 expected by trade-
                                   volume scaling — OOS is BETTER per-trade)
  Recovery rates in OOS window: HIGHER than IS for 4 of 5 strategies.
  No overfitting detected. The rescue calibration is structural (R:R math
  driven), not curve-fit to in-sample noise.

## Risk model

- `RISK_PER_TRADE_PCT = 0.5` of equity, dynamically computed each tick
- `MAX_DAILY_LOSS_PCT = 3.0` halts new entries
- `MAX_TOTAL_DD_PCT = 20.0` halts entirely
- `MAX_OPEN_POSITIONS = 15` cap
- Min-lot safety: skips trades if min-lot would exceed budget (tiny accounts)

## Expected performance (on 8-year backtest)

| At 0.5% risk on... | Expected $/day backtest | Realistic live |
|--------------------|--------------------------|----------------|
| $1,000 account | $0.15-$0.30 | $0.10-$0.20 |
| $10,000 | $1.50-$3 | $1-$2 |
| $100,000 | $15-$30 | $10-$20 |

These are conservative. The bot's max measured drawdown was ~15% on
backtest data covering 2018-2026. Live drawdowns may be larger.

## If anything breaks

Run the three reference backtests in `backtests/` to verify edges still
hold on current data. If OOS results decay below ~50% of documented values,
stop trading and investigate before resuming.
