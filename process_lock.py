"""
Cross-platform single-instance lock via PID file.

Usage:
    from process_lock import acquire_or_die

    acquire_or_die("d1_portfolio_bot")   # raises SystemExit if another instance is alive
    # ... rest of bot code ...

How it works:
  - Writes <name>.pid in the project directory with current process's PID and start time
  - On subsequent runs, reads the file:
      • If PID is alive AND was started before this process → ANOTHER INSTANCE IS RUNNING → exit
      • Otherwise (stale file from a crashed process) → overwrite and continue
  - On normal shutdown, atexit handler removes the file

Works on Windows, Linux, macOS without external dependencies.
"""

import os
import sys
import atexit
import signal
from datetime import datetime, timezone


def _is_pid_alive(pid):
    """Return True if a process with the given PID is currently running."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # Windows: open process with limited access; if it succeeds, process exists
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if handle:
            # Check if process has actually exited (zombie handle)
            exit_code = ctypes.c_ulong()
            STILL_ACTIVE = 259
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return ok and exit_code.value == STILL_ACTIVE
        return False
    else:
        # POSIX: signal 0 doesn't deliver, just checks existence
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def _lock_path(name):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, f"{name}.pid")


def acquire_or_die(name):
    """Acquire the lock or exit with a clear message if another instance is running."""
    path = _lock_path(name)
    our_pid = os.getpid()
    our_start = datetime.now(timezone.utc).isoformat()

    if os.path.exists(path):
        try:
            with open(path) as f:
                content = f.read().strip().split("\n")
            existing_pid = int(content[0])
            existing_start = content[1] if len(content) > 1 else "unknown"
        except (ValueError, IndexError, OSError):
            existing_pid = 0
            existing_start = "unparseable"

        if existing_pid > 0 and existing_pid != our_pid and _is_pid_alive(existing_pid):
            print("=" * 70)
            print(f"!!! ANOTHER INSTANCE IS ALREADY RUNNING !!!")
            print(f"  process: {name}")
            print(f"  existing PID: {existing_pid}  (started {existing_start})")
            print(f"  this PID:     {our_pid}")
            print(f"  lock file:    {path}")
            print()
            print("If you're SURE no other instance is running, delete the .pid file:")
            print(f"  del {path}")
            print("=" * 70)
            sys.exit(2)
        else:
            # Stale lock — log it and overwrite
            if existing_pid > 0:
                print(f"[lock] stale {name}.pid from PID {existing_pid} (not running) — claiming")

    # Write our lock
    try:
        with open(path, "w") as f:
            f.write(f"{our_pid}\n{our_start}\n")
    except OSError as e:
        print(f"[lock] WARNING: could not write lock file {path}: {e}")
        return  # don't die; just lose the protection

    # Ensure cleanup on normal exit OR signal
    def _release():
        try:
            with open(path) as f:
                pid_in_file = int(f.read().strip().split("\n")[0])
            if pid_in_file == our_pid:
                os.remove(path)
        except (FileNotFoundError, ValueError, OSError):
            pass

    atexit.register(_release)
    # Catch SIGTERM (kill) and SIGINT (Ctrl+C) so lock is cleaned up on abrupt stop
    def _signal_handler(signum, frame):
        _release()
        sys.exit(0)
    try:
        signal.signal(signal.SIGINT, _signal_handler)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, OSError):
        pass  # signal handlers can't be set on non-main thread

    print(f"[lock] acquired {name}.pid (PID {our_pid})")
