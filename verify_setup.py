"""
Pre-Live Verification — single command, run BEFORE you `python run_all.py` live.

Confirms:
  1. All Python modules import cleanly (no syntax/import errors)
  2. Required state files exist and parse as valid JSON
  3. All config values look sane (no zeros where there shouldn't be, etc.)
  4. The recently-fixed items are wired correctly
  5. MT5 connection works and account ID matches config (if MT5 reachable)
  6. No stale .pid files claiming bots are alive when they aren't

Run:    python verify_setup.py
Exit:   0 if everything passes, 1 if anything failed.

Designed to be safe to run at ANY time — read-only, no trading actions.
"""

import json
import os
import sys
import importlib


# ANSI colors (work on most terminals; auto-disabled on non-TTY)
def _tty():
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")
G = "\033[92m" if _tty() else ""
R = "\033[91m" if _tty() else ""
Y = "\033[93m" if _tty() else ""
B = "\033[94m" if _tty() else ""
X = "\033[0m"  if _tty() else ""

_failures = []
_warnings = []


def ok(msg):     print(f"  {G}[OK]{X}   {msg}")
def fail(msg):   print(f"  {R}[FAIL]{X} {msg}"); _failures.append(msg)
def warn(msg):   print(f"  {Y}[WARN]{X} {msg}"); _warnings.append(msg)
def section(t):  print(f"\n{B}{t}{X}")


# ============================================================================
# 1. Module imports
# ============================================================================
def check_imports():
    section("[1/6] Importing all bot modules...")
    modules = [
        "indicators", "close_helpers", "peak_equity_store", "process_lock",
        "d1_portfolio_config", "d1_portfolio_strategy", "strategy_health",
        "trade_intelligence", "loser_rescue", "d1_portfolio_bot",
        "monitor_agent", "analytics_agent",
    ]
    for m in modules:
        try:
            importlib.import_module(m)
            ok(f"{m}")
        except Exception as e:
            fail(f"{m}: {type(e).__name__}: {e}")


# ============================================================================
# 2. Config sanity
# ============================================================================
def check_config():
    section("[2/6] Config sanity checks...")
    import d1_portfolio_config as cfg

    # Risk model
    if cfg.USE_DYNAMIC_RISK:
        ok(f"USE_DYNAMIC_RISK = True, RISK_PER_TRADE_PCT = {cfg.RISK_PER_TRADE_PCT}%")
    else:
        warn(f"USE_DYNAMIC_RISK = False, fixed ${cfg.RISK_PER_TRADE_USD}/trade")

    if 0.1 <= cfg.RISK_PER_TRADE_PCT <= 2.0:
        ok(f"RISK_PER_TRADE_PCT in sensible range")
    else:
        warn(f"RISK_PER_TRADE_PCT={cfg.RISK_PER_TRADE_PCT}% is outside 0.1-2.0 range")

    # Halts
    if cfg.MAX_DAILY_LOSS_PCT > 0 and cfg.MAX_TOTAL_DD_PCT > cfg.MAX_DAILY_LOSS_PCT:
        ok(f"MAX_DAILY_LOSS_PCT={cfg.MAX_DAILY_LOSS_PCT}%, MAX_TOTAL_DD_PCT={cfg.MAX_TOTAL_DD_PCT}%")
    else:
        fail("Halt thresholds inverted or zero")

    if cfg.MAX_OPEN_POSITIONS >= 5:
        ok(f"MAX_OPEN_POSITIONS = {cfg.MAX_OPEN_POSITIONS}")
    else:
        warn(f"MAX_OPEN_POSITIONS = {cfg.MAX_OPEN_POSITIONS} (very tight)")

    # Rescue layer
    if cfg.USE_LOSER_RESCUE:
        ok(f"USE_LOSER_RESCUE = True (keep>={cfg.RESCUE_KEEP_THRESHOLD}, "
           f"hard-close>={cfg.RESCUE_HARD_CLOSE_PCT}%)")
    else:
        warn("USE_LOSER_RESCUE = False — legacy 70% bucket rule active")

    if 30 <= cfg.RESCUE_KEEP_THRESHOLD <= 70:
        ok(f"RESCUE_KEEP_THRESHOLD = {cfg.RESCUE_KEEP_THRESHOLD}")
    else:
        warn(f"RESCUE_KEEP_THRESHOLD = {cfg.RESCUE_KEEP_THRESHOLD} (unusual)")

    if cfg.RESCUE_HARD_CLOSE_PCT >= 95.0:
        ok(f"RESCUE_HARD_CLOSE_PCT = {cfg.RESCUE_HARD_CLOSE_PCT}% (calibrated to 99)")
    else:
        warn(f"RESCUE_HARD_CLOSE_PCT = {cfg.RESCUE_HARD_CLOSE_PCT}% — backtest showed <95 costs money")

    # Quality filter
    if cfg.USE_QUALITY_FILTER:
        ok(f"USE_QUALITY_FILTER = True (threshold={cfg.QUALITY_THRESHOLD})")
    else:
        warn("USE_QUALITY_FILTER = False — all signals get fired")

    # Bucket TP
    if cfg.USE_BUCKET_TP:
        ok(f"USE_BUCKET_TP = True, mode='{cfg.BUCKET_TP_MODE}'  "
           f"(min=${cfg.BUCKET_TP_MIN}, max=${cfg.BUCKET_TP_MAX})")
    else:
        warn("USE_BUCKET_TP = False — no daily-profit lock")

    # Agent thresholds (now centralized)
    if cfg.MAX_SAME_CURRENCY_POS >= 6:
        ok(f"MAX_SAME_CURRENCY_POS = {cfg.MAX_SAME_CURRENCY_POS} (tuned post-audit)")
    else:
        warn(f"MAX_SAME_CURRENCY_POS = {cfg.MAX_SAME_CURRENCY_POS} — may fire on routine clustering")
    if cfg.CORR_GUARD_LOSING_THRESH <= -200:
        ok(f"CORR_GUARD_LOSING_THRESH = ${cfg.CORR_GUARD_LOSING_THRESH}")
    else:
        warn(f"CORR_GUARD_LOSING_THRESH = ${cfg.CORR_GUARD_LOSING_THRESH} (was -$50 pre-audit)")
    if cfg.CORR_MIN_AGE_SECONDS >= 7200:
        ok(f"CORR_MIN_AGE_SECONDS = {cfg.CORR_MIN_AGE_SECONDS}s ({cfg.CORR_MIN_AGE_SECONDS//3600}h)")
    else:
        warn(f"CORR_MIN_AGE_SECONDS = {cfg.CORR_MIN_AGE_SECONDS}s — fresh positions vulnerable")

    # Active combinations
    n_combos = len(cfg.ACTIVE_COMBINATIONS)
    if n_combos >= 50:
        ok(f"ACTIVE_COMBINATIONS = {n_combos} (sym, strat) entries")
    else:
        warn(f"ACTIVE_COMBINATIONS = {n_combos} — fewer combos than expected")


# ============================================================================
# 3. State files
# ============================================================================
def check_state_files():
    section("[3/6] Runtime state files...")
    state_files = {
        "strategy_health.json":   "per-strategy health counters",
        "strategy_cooldown.json": "agent-imposed re-entry cooldowns",
        "peak_equity.json":       "shared drawdown baseline",
        "mfe_mae.json":           "per-position MFE/MAE tracker",
    }
    for path, purpose in state_files.items():
        if not os.path.exists(path):
            warn(f"{path} not found ({purpose}) — will be created on first run")
            continue
        try:
            with open(path) as f:
                data = json.load(f)
            n = len(data) if isinstance(data, dict) else 0
            ok(f"{path}  {n} entries  ({purpose})")
        except json.JSONDecodeError as e:
            fail(f"{path} is invalid JSON: {e}")
        except Exception as e:
            fail(f"{path}: {e}")

    # peak_equity_store sanity
    try:
        import peak_equity_store as peak_store
        v = peak_store.load_peak(default=0.0)
        if v > 0:
            ok(f"peak_equity_store.load_peak() = ${v:.2f}")
        else:
            warn("peak_equity_store has no peak yet (first run?)")
    except Exception as e:
        fail(f"peak_equity_store.load_peak() raised: {e}")


# ============================================================================
# 4. Recently-fixed items wired correctly
# ============================================================================
def check_audit_fixes():
    section("[4/6] Audit fixes are wired in...")
    import d1_portfolio_bot as bot
    import monitor_agent as agent
    import loser_rescue as rescue
    import strategy_health as health
    import d1_portfolio_config as cfg

    # Fix 1: correlation guard skips winners — verify the source line exists
    import inspect
    agent_src = inspect.getsource(agent.check_correlation_exposure)
    if "losing_mature" in agent_src and "p.profit + p.swap) < 0" in agent_src:
        ok("Correlation guard filters to losers only (skips winners)")
    else:
        fail("Correlation guard winner-skip filter not found")

    # Fix 2: peak_equity_store imported by both bot and agent
    if "peak_equity_store" in inspect.getsource(bot) and "peak_equity_store" in inspect.getsource(agent):
        ok("Bot + agent both use shared peak_equity_store")
    else:
        fail("Shared peak_equity_store not wired into both processes")

    # Fix 3: rescue uses original_sl
    rescue_src = inspect.getsource(rescue.update_mfe_mae)
    if "original_sl" in rescue_src:
        ok("MFE/MAE tracker captures original_sl (BE-move safe)")
    else:
        fail("original_sl capture missing in MFE/MAE tracker")

    # Fix 4: bot cooldown after bucket close
    bot_src = inspect.getsource(bot.smart_bucket_close)
    if "_write_bot_cooldown" in bot_src:
        ok("Smart bucket close writes cooldown")
    else:
        fail("Smart bucket close cooldown not wired in")

    # Fix 5: stale cleanup skips winners
    stale_src = inspect.getsource(agent.check_stale_positions)
    if "p.profit + p.swap) > 0" in stale_src:
        ok("Stale cleanup skips winners")
    else:
        fail("Stale cleanup still closes winners")

    # Fix 6: per-strategy WR floor
    if hasattr(health, "_deactivate_wr_floor"):
        floors = {s: health._deactivate_wr_floor(s) for s in
                  ["donchian20", "momentum60", "rsi2", "consensus"]}
        if len(set(floors.values())) > 1:   # they should differ per strategy
            ok(f"Per-strategy WR floors active: {floors}")
        else:
            warn("Per-strategy WR floor present but values are uniform")
    else:
        fail("_deactivate_wr_floor function missing from strategy_health")

    # Indicators centralized
    import indicators
    if all(hasattr(indicators, fn) for fn in ("atr", "rsi", "sma", "bollinger", "ema",
                                                "atr_at", "rsi_at", "sma_at")):
        ok("indicators.py exports all 8 expected functions")
    else:
        fail("indicators.py missing one or more expected functions")

    # close_helpers centralized
    import close_helpers
    if hasattr(close_helpers, "send_close_request"):
        ok("close_helpers.send_close_request available")
    else:
        fail("close_helpers.send_close_request missing")

    # SL-migration event logger
    import trade_intelligence as ti
    if hasattr(ti, "_log_migration_event"):
        ok(f"SL-migration event logger wired (target: {ti._MIGRATION_LOG_FILE})")
    else:
        fail("SL-migration event logger not wired into trade_intelligence")

    # Rescue measured table covers all active combos
    cfg_strats = sorted({s for _, s in cfg.ACTIVE_COMBINATIONS})
    covered = []
    missing = []
    for s in cfg_strats:
        r = rescue._family_default_recovery(s)
        if r in (0.13, 0.14, 0.12) and s not in rescue._MEASURED_RECOVERY:
            # Got a family fallback, not a measured value
            covered.append((s, r, "fallback"))
        else:
            covered.append((s, r, "measured"))
    fallback_count = sum(1 for _, _, k in covered if k == "fallback")
    if fallback_count == 0:
        ok(f"All {len(cfg_strats)} active strategies have measured recovery rates")
    else:
        warn(f"{fallback_count}/{len(cfg_strats)} strategies use family fallback "
             f"(not measured): {[s for s, _, k in covered if k=='fallback']}")


# ============================================================================
# 5. PID file sanity
# ============================================================================
def check_pid_files():
    section("[5/6] PID files (process locks)...")
    import process_lock
    pid_files = ["d1_portfolio_bot.pid", "monitor_agent.pid", "analytics_agent.pid"]
    any_alive = False
    for pf in pid_files:
        if not os.path.exists(pf):
            ok(f"{pf} not present (process not running)")
            continue
        try:
            with open(pf) as f:
                pid_line = f.readline().strip()
            pid = int(pid_line)
            alive = process_lock._is_pid_alive(pid)
            if alive:
                warn(f"{pf} -> PID {pid} is ALIVE — bot is running. "
                     f"Stop with Ctrl+C before launching new code.")
                any_alive = True
            else:
                ok(f"{pf} -> PID {pid} is stale (will be auto-cleaned on next start)")
        except Exception as e:
            warn(f"{pf}: could not parse: {e}")
    if any_alive:
        print(f"\n  {Y}NOTE:{X} bots are currently running. To pick up new code:")
        print(f"        1) Ctrl+C the run_all.py terminal")
        print(f"        2) Wait for '>>> all stopped'")
        print(f"        3) python run_all.py")


# ============================================================================
# 6. MT5 connectivity (best-effort, won't fail if MT5 not installed)
# ============================================================================
def check_mt5():
    section("[6/6] MetaTrader 5 connectivity (best-effort)...")
    try:
        import MetaTrader5 as mt5
    except ImportError:
        warn("MetaTrader5 package not installed — `pip install MetaTrader5`")
        return
    if not mt5.initialize():
        warn(f"mt5.initialize() failed: {mt5.last_error()}")
        warn("This is fine if MT5 terminal isn't running yet.")
        return
    try:
        acct = mt5.account_info()
        if acct is None:
            warn("mt5.account_info() returned None — not logged in")
        else:
            from d1_portfolio_config import ACCT_NO
            if acct.login == ACCT_NO:
                ok(f"Connected to account {acct.login} (matches config) "
                   f"equity=${acct.equity:.2f} {acct.currency}")
            else:
                fail(f"Connected to account {acct.login} but config says {ACCT_NO}. "
                     f"Bot will refuse to trade.")
    finally:
        mt5.shutdown()


# ============================================================================
# Entry point
# ============================================================================
def main():
    print("=" * 70)
    print("Pre-Live Verification — D1 Portfolio Trading System")
    print("=" * 70)
    check_imports()
    check_config()
    check_state_files()
    check_audit_fixes()
    check_pid_files()
    check_mt5()
    print()
    print("=" * 70)
    if _failures:
        print(f"{R}FAILED:{X} {len(_failures)} hard error(s):")
        for f in _failures:
            print(f"  - {f}")
        if _warnings:
            print(f"\n{Y}WARNINGS:{X} {len(_warnings)}")
        print("\nDO NOT start the bot until failures are fixed.")
        sys.exit(1)
    elif _warnings:
        print(f"{Y}PASSED with {len(_warnings)} warning(s).{X}")
        print("Review warnings above. They are non-fatal but worth understanding.")
        print("Safe to start with: python run_all.py")
        sys.exit(0)
    else:
        print(f"{G}PASSED — all checks clean.{X}")
        print("Safe to start with: python run_all.py")
        sys.exit(0)


if __name__ == "__main__":
    main()
