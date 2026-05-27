"""
D1 Portfolio Bot — config (v10 expanded).

Measured across 8 years of real data, 23 instruments, 7 strategies:
  62 winning (symbol, strategy) combinations
  Backtest: +$16.79/day at $50/trade risk
  OOS:      +$32,428 total
  Scale to $100/day: $298 risk per trade

Tight-TP variants (donchian20_T, momentum60_T) give higher win rates with
smaller faster wins — they're separate magic numbers from original variants
so they can run alongside without conflict.
"""

from datetime import datetime
import MetaTrader5 as mt5


# ---------------------------------------------------------------------------
# Account / safety
# ---------------------------------------------------------------------------
ACCT_NO              = 107185456
MAGIC_BASE           = 19770800
EXP_DATE             = datetime(2029, 9, 10)

# ---------------------------------------------------------------------------
# Risk — DYNAMIC: scales automatically with your account equity
# ---------------------------------------------------------------------------
# Set USE_DYNAMIC_RISK = True to risk a PERCENTAGE of current equity per trade
# (recommended — auto-scales for any account size from $100 to $1M).
# Set False to use the legacy fixed dollar amount in RISK_PER_TRADE_USD.
#
# Example with USE_DYNAMIC_RISK=True, RISK_PER_TRADE_PCT=0.5:
#   $100 account   -> $0.50 risk per trade   (but min-lot may force skip)
#   $1,000 account -> $5 risk per trade
#   $10,000 account-> $50 risk per trade
#   $100,000 account -> $500 risk per trade
USE_DYNAMIC_RISK     = True
RISK_PER_TRADE_PCT   = 0.5       # 0.5% of equity per trade (industry-standard)

# Legacy/fallback: used only if USE_DYNAMIC_RISK = False
RISK_PER_TRADE_USD   = 50.0

# Daily loss cap — also dynamic when USE_DYNAMIC_RISK is True.
# 3.0% caps you at ~6× single-trade risk before pausing for the day.
MAX_DAILY_LOSS_PCT   = 3.0       # halt new entries if today's loss > 3% of equity
MAX_DAILY_LOSS_USD   = 300.0     # legacy fixed cap (used if USE_DYNAMIC_RISK = False)

MAX_OPEN_POSITIONS   = 15
MAX_TOTAL_DD_PCT     = 20.0      # halt if equity drops > 20% from peak

# Daily trade-count cap — prevents the bot from opening dozens of trades in a
# single day even if signals keep firing. Resets at UTC midnight along with
# day_pnl. Set to 0 to disable (cap = MAX_OPEN_POSITIONS only).
MAX_TRADES_OPENED_PER_DAY = 30

# ---------------------------------------------------------------------------
# Trade Intelligence Layer — pre-trade quality filter + post-entry SL migration
# ---------------------------------------------------------------------------
# QUALITY FILTER (pre-trade): each candidate trade gets a 0-100 score from
# 6 components:
#   live_wr (25%)   — live WR vs backtest expectation
#   health (15%)    — strategy_health.is_active()
#   spread (15%)    — spread/TP-distance ratio
#   regime (15%)    — ATR percentile (avoid extremes)
#   mtf (15%)       — higher-TF trend agreement
#   concurrent (15%)— other strategies agreeing same direction
# Trades scoring below QUALITY_THRESHOLD are silently dropped.
#
# NOTE: QUALITY_THRESHOLD (60) and RESCUE_KEEP_THRESHOLD (45) are NOT directly
# comparable — they grade different things on different sub-score weights:
#   QUALITY (pre-trade):  "is this entry good enough to take?"      strict
#   RESCUE  (post-loser): "given we're already in, is this position
#                          likely enough to recover that closing it
#                          at -0.7R during a bucket sweep is the wrong
#                          call?"                                    permissive
# Asymmetry is intentional: declining to enter is free; closing an existing
# trade locks in a real loss. Different cost structures → different bars.
USE_QUALITY_FILTER       = True
QUALITY_THRESHOLD        = 60      # 0-100 (60 = medium-strict)
QUALITY_VERBOSE          = False   # set True to log every score breakdown

# SL MIGRATION: when a position is 50% of the way to TP, move SL to entry+buffer.
# Trade can no longer become a losing trade. Original TP unchanged.
# Also: when a position ages past 70% of its strategy's max_hold without resolving,
# tighten TP toward current price IF currently profitable (locks small win).
USE_SL_MIGRATION         = True
SL_MIGRATION_TRIGGER     = 0.50    # 0.50 = halfway to TP triggers BE move
SL_MIGRATION_BUFFER_PT   = 0.5     # SL goes to entry + this many points (small lock-in)

# Maximum TP distance (% of entry price) — caps "wide TP" outliers like
# rsi2-on-XAUUSD that wants gold to move 5%+. Keeps targets achievable in
# reasonable time. SL is NOT capped — that protects you.
# Set to a very large number (e.g. 99.0) to disable the cap.
USE_MAX_TP_PCT           = True
MAX_TP_PCT_FX            = 2.0     # FX pairs: max 2% TP distance
MAX_TP_PCT_METALS        = 3.0     # XAUUSD/XAGUSD/XPTUSD/XPDUSD: max 3%
MAX_TP_PCT_INDICES       = 1.5     # US500/US30/UK100: max 1.5%

# ---------------------------------------------------------------------------
# Per-symbol exposure cap
# ---------------------------------------------------------------------------
# Default 99 = effectively unlimited.  Each (symbol, strategy) combo already
# self-limits to 1 open position via the existing duplicate-check, so 4
# strategies on XAUUSD naturally produces max 4 positions. Set this to a
# smaller number if you want extra restriction on a single pair's exposure.
MAX_POSITIONS_PER_SYMBOL = 99

# Per-symbol RISK OVERRIDE — for volatile/illiquid pairs, use lower risk %.
# Symbol not listed = uses the default RISK_PER_TRADE_PCT.
# Set value as a multiplier of default risk (e.g. 0.6 = 60% of default).
RISK_MULTIPLIER_BY_SYMBOL = {
    "XAUUSD": 0.6,   # gold is volatile — use 60% of normal risk
    "XAGUSD": 0.6,   # silver is even more volatile
    "XPTUSD": 0.5,   # platinum — new, lower size while validating
    "XPDUSD": 0.5,   # palladium — new, lower size while validating
    "GBPJPY": 0.8,   # cross-yen pairs have higher ranges
    "GBPAUD": 0.8,
    "EURJPY": 0.8,
    "AUDJPY": 0.9,
    # All others default to 1.0 (no adjustment)
}

# Min lot safety: if the broker's minimum lot would require MORE risk than
# RISK_PER_TRADE_PCT × equity, the bot will SKIP the trade. Prevents over-risk
# on tiny accounts where 0.01 lot may exceed % budget on volatile symbols.

# ---------------------------------------------------------------------------
# Bucket TP MODE: how the bucket target is computed
#   "fixed"        — always BUCKET_TP_USD (the legacy behavior)
#   "per_position" — scale target with number of open positions (recommended)
#   "pct_equity"   — scale target as % of account equity
# ---------------------------------------------------------------------------
BUCKET_TP_MODE        = "per_position"

# per_position parameters (used when BUCKET_TP_MODE == "per_position"):
# Target = clamp(positions_open × BUCKET_TP_PER_POS, BUCKET_TP_MIN, BUCKET_TP_MAX)
# Example with PER_POS=40, MIN=80, MAX=500:
#    2 positions → target $80 (floor)
#    6 positions → target $240
#   10 positions → target $400
#   15 positions → target $500 (ceiling)
BUCKET_TP_PER_POS     = 25.0
BUCKET_TP_MIN         = 200.0
BUCKET_TP_MAX         = 250.0

# pct_equity parameters (used when BUCKET_TP_MODE == "pct_equity"):
BUCKET_TP_EQUITY_PCT  = 0.3      # 0.3% of equity

# ---------------------------------------------------------------------------
# Bucket TP — lock in daily profits at a target, close ALL positions
# ---------------------------------------------------------------------------
# When enabled: bot tracks total floating P&L of its positions every tick.
# Once floating P&L >= BUCKET_TP_USD, close ALL positions in one sweep.
# Trades resume on the next bar close as normal.
#
# Trade-off:
#   PRO: Consistent daily cash flow, locks in good days early
#   PRO: Removes "winner turned loser" frustration
#   CON: Caps upside — some days strategies would naturally make $300+
#   CON: ~10-20% reduction in long-term expectancy per backtest
#
# Recommendation: leave OFF for first month to see natural performance;
# enable later if you prefer consistency over maximum theoretical gains.
USE_BUCKET_TP        = True
# NOTE: BUCKET_TP_USD is ONLY used when BUCKET_TP_MODE == "fixed" above.
# With the recommended "per_position" mode (active), the ACTIVE target is
# computed dynamically from PER_POS, MIN, MAX above — this value is ignored.
BUCKET_TP_USD        = 150.0     # legacy fallback (used only if MODE="fixed")
BUCKET_COOLDOWN_SEC  = 60     # don't re-arm bucket for this many seconds after firing

# Bucket SMART close — when bucket TP fires, instead of closing ALL positions:
#   • Close all WINNERS (lock in profit)
#   • Close losers that are within BUCKET_CLOSE_LOSER_NEAR_SL_PCT % of SL
#     (cut losses about to hit anyway)
#   • LEAVE mild losers open, giving them time to recover to TP
USE_SMART_BUCKET_CLOSE         = True
BUCKET_CLOSE_LOSER_NEAR_SL_PCT = 70.0   # losers >= 70% of SL distance are EVALUATED for closing

# ---------------------------------------------------------------------------
# Loser Rescue Intelligence Layer
# ---------------------------------------------------------------------------
# When a loser crosses BUCKET_CLOSE_LOSER_NEAR_SL_PCT, the old rule was: close it.
# The rescue layer replaces that binary call with a 0-100 recovery probability
# score blending: live per-(sym, strat) recovery rate, MFE proximity to TP,
# MTF alignment, ATR regime, and how deep into SL territory we are.
# Losers scoring >= RESCUE_KEEP_THRESHOLD are KEPT (let them ride to natural
# resolution); below threshold are closed as before.
USE_LOSER_RESCUE          = True       # master switch; False → falls back to old 70% rule
RESCUE_KEEP_THRESHOLD     = 45         # 0-100, lower = keep more losers (backtest-tuned from 55)
RESCUE_MIN_SAMPLES        = 10         # below this, use bootstrap defaults (calibrated from 8-yr data)
RESCUE_HARD_CLOSE_PCT     = 99.0       # consumed_pct above this → always close. Sweep on 8-yr data
                                       # showed every threshold below 99 cost money vs no hard-close;
                                       # 99 retains a safety net for tick-divergence without harming
                                       # measured P&L. Set 100+ to disable entirely.
RESCUE_LOG_DECISIONS      = True       # CSV log every keep/close decision for post-hoc analysis

# After the bot's smart_bucket_close closes a (sym, strat), block re-entry for
# this many hours to prevent immediate churn. Mirrors the agent's
# COOLDOWN_AFTER_AGENT_CLOSE_HOURS (4h) but shorter — bot bucket fires are
# routine, not emergencies.
BOT_COOLDOWN_AFTER_CLOSE_HOURS = 1.0

# ---------------------------------------------------------------------------
# Monitor agent thresholds (centralized 2026-05-26 — were previously
# hard-coded inside monitor_agent.py). Single source of truth for risk policy.
# ---------------------------------------------------------------------------
# Cadence
AGENT_POLL_SECONDS            = 10           # main loop cadence

# Emergency triggers — TIGHTER than the bot's own caps so they only fire
# in situations the bot's circuit breakers wouldn't catch.
CRIT_MARGIN_LEVEL_PCT         = 200.0        # below this = pre-margin-call zone
EMERGENCY_DD_PCT              = 15.0         # intraday equity DD% → close all
EMERGENCY_SPREAD_MULT         = 5.0          # spread > Nx baseline = flash event
SPREAD_PERSISTENT_TICKS       = 3            # require N consecutive breach ticks
EMERGENCY_COOLDOWN_SEC        = 300          # min interval between emergency fires

# Correlation guard — caps simultaneous same-currency exposure.
# Re-tuned 2026-05-26 to fire only on genuine concentration emergencies.
MAX_SAME_CURRENCY_POS         = 7            # >N positions on same currency triggers
CORR_GUARD_LOSING_THRESH      = -500.0       # only act if collectively losing > this
CORR_MIN_AGE_SECONDS          = 14400        # 4 hrs — don't touch fresh positions

# Stale position cleanup
STALE_HOURS_H1                = 72           # H1 trades open > 72h = stale
STALE_DAYS_D1                 = 60           # D1 trades open > 60d = stale

# Adaptive risk — halve bot's per-trade risk when agent sees portfolio DD
DRAWDOWN_RISK_HALVE_PCT       = 5.0          # ≥ this DD% → write 0.5× multiplier
DRAWDOWN_RECOVERY_PCT         = 2.0          # ≤ this DD% → restore 1.0× multiplier

# Cooldown the agent writes after force-closing a (sym, strat)
COOLDOWN_AFTER_AGENT_CLOSE_HOURS = 4         # block bot re-entry for N hours

# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
# Bot wakes up every POLL_SECONDS to check: bucket TP, news flag, account state,
# bar closes for new signals. Lower = tighter bucket trigger, very slight CPU
# overhead. 5s is a good balance (bucket fires within ~5s of crossing target).
POLL_SECONDS         = 5.0
DEVIATION_OPEN       = 30
DEVIATION_CLOSE      = 150
MIN_LOT              = 0.01
MAX_LOT              = 100.0

# ---------------------------------------------------------------------------
# Strategy parameters
# ---------------------------------------------------------------------------
ATR_PERIOD           = 14
DONCHIAN_LOOKBACK    = 20
MOMENTUM_LOOKBACK    = 60
RSI2_OVERSOLD        = 10
RSI2_OVERBOUGHT      = 90
TREND_MA_PERIOD      = 200
BB_PERIOD            = 20
BB_STD               = 2.5

# Original variants — bigger TPs, fewer wins, larger payouts
STRAT_PARAMS = {
    "donchian20":  {"sl_atr": 2.0, "tp_atr_mult": 3.0, "max_hold_d": 40},
    "momentum60":  {"sl_atr": 2.0, "tp_atr_mult": 1.5, "max_hold_d": 60},
    "rsi2":        {"sl_atr": 2.0, "tp_atr_mult": 0.75, "max_hold_d": 15},
    "3day_reverse":{"sl_atr": 1.5, "tp_atr_mult": 0.67, "max_hold_d": 10},
    "bb_extreme":  {"sl_atr": 1.5, "tp_atr_mult": 0.0,  "max_hold_d": 15},
}

# ---------------------------------------------------------------------------
# 62 surviving (symbol, strategy) combinations from backtest_v10
# Includes 10 NEW symbols (CADJPY, CHFJPY, NZDJPY, EURCHF, EURAUD, EURCAD,
# GBPAUD, AUDCAD) + tight-TP variants
# ---------------------------------------------------------------------------
ACTIVE_COMBINATIONS = [
    # ==== CONSENSUS — multi-strategy confirmation (highest-quality D1 trades) ====
    # Fires only when ≥1 mean-reversion + ≥1 trend strategy agree on direction.
    # Backtest: +36% improvement in $/day vs single-strategy at similar win rate.
    # Platinum & Palladium — same strategies that work on XAUUSD/XAGUSD
    # (volatile precious metals with clean trending behavior). NOTE: these
    # haven't been backtested individually — start at lower risk to validate.
    ("XPTUSD", "consensus"),
    ("XPDUSD", "consensus"),
    ("XAUUSD", "consensus"),
    ("XAGUSD", "consensus"),
    ("US500",  "consensus"),
    ("US30",   "consensus"),
    ("EURUSD", "consensus"),
    ("GBPUSD", "consensus"),
    ("USDJPY", "consensus"),
    ("GBPJPY", "consensus"),
    ("AUDJPY", "consensus"),
    ("EURJPY", "consensus"),
    ("AUDUSD", "consensus"),
    ("NZDUSD", "consensus"),
    ("EURGBP", "consensus"),
    ("CADJPY", "consensus"),
    ("CHFJPY", "consensus"),
    ("NZDJPY", "consensus"),
    ("EURCHF", "consensus"),
    ("EURAUD", "consensus"),
    ("EURCAD", "consensus"),
    ("GBPAUD", "consensus"),
    ("AUDCAD", "consensus"),

    # ==== H1 strategies — faster trade closure (4-48 hour holds, mostly intraday) ====
    # 15 winners from backtest_v12, combined +$337/day at $300/trade risk.
    ("XAUUSD", "momentum60_H1"),     # +$56/day (top performer)
    ("XAGUSD", "momentum60_H1"),     # +$40/day
    ("XPTUSD", "momentum60_H1"),     # similar metals — added 2026-05-25
    ("XPDUSD", "momentum60_H1"),
    ("XAGUSD", "donchian20_H1"),     # +$37/day
    ("XAUUSD", "rsi2_H1"),           # +$32/day
    ("AUDJPY", "bb_extreme_H1"),     # +$27/day
    ("GBPAUD", "rsi2_H1"),           # +$24/day
    ("EURCHF", "bb_extreme_H1"),     # +$23/day
    ("EURAUD", "bb_extreme_H1"),     # +$22/day
    ("NZDJPY", "rsi2_H1"),           # +$19/day
    ("US500",  "rsi2_H1"),           # +$15/day
    ("GBPAUD", "bb_extreme_H1"),     # +$15/day
    ("US30",   "rsi2_H1"),           # +$11/day
    ("NZDUSD", "bb_extreme_H1"),     # +$11/day
    ("CADJPY", "bb_extreme_H1"),     # +$4/day
    ("GBPJPY", "bb_extreme_H1"),     # +$2/day

    # ==== TIGHT-TP variants (smaller faster wins) ====
    ("AUDJPY", "donchian20_T"),
    ("AUDJPY", "momentum60_T"),
    ("AUDUSD", "momentum60_T"),
    ("CADJPY", "momentum60_T"),
    ("CHFJPY", "momentum60_T"),
    ("EURJPY", "momentum60_T"),
    ("GBPAUD", "momentum60_T"),
    ("GBPJPY", "momentum60_T"),
    ("GBPUSD", "momentum60_T"),
    ("NZDJPY", "momentum60_T"),
    ("US30",   "donchian20_T"),
    ("US500",  "donchian20_T"),
    ("US500",  "momentum60_T"),
    ("USDJPY", "momentum60_T"),
    ("XAGUSD", "donchian20_T"),
    ("XAUUSD", "donchian20_T"),
    ("XAUUSD", "momentum60_T"),
    # ==== Original-TP variants (bigger payouts, fewer wins) ====
    ("AUDCAD", "rsi2"),
    ("AUDJPY", "donchian20"),
    ("AUDJPY", "momentum60"),
    ("AUDJPY", "rsi2"),
    ("AUDUSD", "bb_extreme"),
    ("AUDUSD", "momentum60"),
    ("CADJPY", "momentum60"),
    ("CADJPY", "rsi2"),
    ("CHFJPY", "bb_extreme"),
    ("CHFJPY", "momentum60"),
    ("EURAUD", "bb_extreme"),
    ("EURCAD", "bb_extreme"),
    ("EURCAD", "rsi2"),
    ("EURCHF", "3day_reverse"),
    ("EURCHF", "bb_extreme"),
    ("EURCHF", "rsi2"),
    ("EURGBP", "bb_extreme"),
    ("EURJPY", "bb_extreme"),
    ("EURJPY", "momentum60"),
    ("EURUSD", "3day_reverse"),
    ("EURUSD", "bb_extreme"),
    ("GBPAUD", "bb_extreme"),
    ("GBPAUD", "momentum60"),
    ("GBPJPY", "bb_extreme"),
    ("GBPJPY", "momentum60"),
    ("GBPJPY", "rsi2"),
    ("GBPUSD", "3day_reverse"),
    ("GBPUSD", "bb_extreme"),
    ("GBPUSD", "momentum60"),
    ("NZDJPY", "bb_extreme"),
    ("NZDUSD", "3day_reverse"),
    ("NZDUSD", "bb_extreme"),
    ("US30",   "bb_extreme"),
    ("US500",  "bb_extreme"),
    ("US500",  "donchian20"),
    ("US500",  "momentum60"),
    ("USDJPY", "3day_reverse"),
    ("USDJPY", "bb_extreme"),
    ("USDJPY", "momentum60"),
    ("XAGUSD", "bb_extreme"),
    ("XAGUSD", "donchian20"),
    ("XAGUSD", "rsi2"),
    ("XAUUSD", "donchian20"),
    ("XAUUSD", "momentum60"),
    ("XAUUSD", "rsi2"),
]

VERBOSE              = True
