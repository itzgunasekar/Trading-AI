"""
Single-command launcher — runs the bot, monitor agent, and analytics agent
in one terminal. Each process's output is prefixed with a label so you can
tell who said what.

Usage:
    python run_all.py

Ctrl+C cleanly stops all three. Logs from each process are also written to
files under logs/ so you can review them later.
"""

import os
import sys
import time
import signal
import threading
import subprocess
from datetime import datetime

PROCESSES = [
    # (label, color_code, script_filename, log_filename)
    ("BOT", "\033[92m",   "d1_portfolio_bot.py", "logs/bot.log"),       # green
    ("MON", "\033[93m",   "monitor_agent.py",    "logs/monitor.log"),   # yellow
    ("AN ", "\033[96m",   "analytics_agent.py",  "logs/analytics.log"), # cyan
]
RESET = "\033[0m"

# Disable ANSI colors on non-color terminals (Windows cmd, redirected output, etc.)
if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
    for i, (lbl, _, scr, log) in enumerate(PROCESSES):
        PROCESSES[i] = (lbl, "", scr, log)
    RESET = ""

PYTHON_EXE = sys.executable
running = True
procs = []


def ensure_logs_dir():
    if not os.path.exists("logs"):
        os.makedirs("logs")


def stream_output(label, color, proc, log_path):
    """Read subprocess stdout line-by-line; print with label and write to log file."""
    with open(log_path, "a", encoding="utf-8") as log_f:
        log_f.write(f"\n\n===== {label} started at {datetime.now().isoformat()} =====\n")
        log_f.flush()
        for line in iter(proc.stdout.readline, b""):
            if not running:
                break
            try:
                text = line.decode("utf-8", errors="replace").rstrip()
            except Exception:
                text = str(line).rstrip()
            if not text:
                continue
            print(f"{color}[{label}]{RESET} {text}", flush=True)
            log_f.write(text + "\n")
            log_f.flush()


def launch(label, color, script, log_path):
    """Start a subprocess running the given script."""
    print(f"  starting {label} -> {script}")
    # bufsize=0 unbuffered on binary stream; child python uses -u so its stdout
    # is also unbuffered. Avoids the "line buffering not supported in binary mode"
    # warning on Python 3.14.
    proc = subprocess.Popen(
        [PYTHON_EXE, "-u", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    procs.append((label, proc))
    t = threading.Thread(target=stream_output,
                          args=(label, color, proc, log_path), daemon=True)
    t.start()
    return proc


def shutdown(signum=None, frame=None):
    global running
    if not running:
        return
    running = False
    print("\n>>> shutting down all processes...")
    for label, p in procs:
        if p.poll() is None:
            print(f"  stopping {label}...")
            try:
                p.terminate()
            except Exception:
                pass
    # Give them a moment to exit cleanly
    deadline = time.time() + 5
    for label, p in procs:
        remaining = deadline - time.time()
        if remaining > 0:
            try:
                p.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                pass
    # Force-kill any that didn't comply
    for label, p in procs:
        if p.poll() is None:
            print(f"  force-killing {label}")
            try:
                p.kill()
            except Exception:
                pass
    print(">>> all stopped")


def main():
    # Always operate from the directory this script lives in, so users can
    # invoke it from anywhere (e.g. `python D:/Ajith/diff_ea_ai/run_all.py`).
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    ensure_logs_dir()
    print("=" * 70)
    print("Unified Launcher — D1 Portfolio Trading System")
    print("=" * 70)
    print(f"Python: {PYTHON_EXE}")
    print(f"Working dir: {os.getcwd()}")
    print(f"Logs: {os.path.abspath('logs/')}")
    print("Ctrl+C to stop all processes")
    print("=" * 70)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    for label, color, script, log_path in PROCESSES:
        if not os.path.exists(script):
            print(f"  WARNING: {script} not found, skipping {label}")
            continue
        launch(label, color, script, log_path)
        time.sleep(2)   # stagger launches to avoid MT5 connection races

    # Main loop — keep parent alive, watch for child deaths
    try:
        while running:
            time.sleep(2)
            for label, p in list(procs):
                if p.poll() is not None:
                    print(f">>> [{label}] process exited with code {p.returncode}")
                    procs.remove((label, p))
                    # If a critical one dies (bot), shut everything down
                    if label == "BOT":
                        print(">>> bot died — shutting down others")
                        shutdown()
                        return
            if not procs:
                print(">>> all processes exited, leaving launcher")
                return
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
