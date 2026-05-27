"""
Shared peak-equity store — read/written by BOTH the bot and the monitor agent
so drawdown calculations agree across process restarts.

Format (peak_equity.json):
  {
    "peak_equity": 100450.32,
    "updated_at_iso": "2026-05-26T13:55:00+00:00",
    "updated_by": "bot" | "agent"
  }

Convention: peak is monotonically non-decreasing — it represents the highest
equity ever observed by either process. Both processes read on startup and
update each tick if their observed equity exceeds the file value.
"""

import json
import os
from datetime import datetime, timezone

PEAK_FILE = "peak_equity.json"


def load_peak(default=0.0):
    if not os.path.exists(PEAK_FILE):
        return default
    try:
        with open(PEAK_FILE) as f:
            data = json.load(f)
        return float(data.get("peak_equity", default))
    except Exception:
        return default


def update_peak(observed_equity, source="bot"):
    """Atomically raise the persisted peak if observed_equity is higher.
    Returns the (possibly updated) peak value."""
    current = load_peak(default=0.0)
    if observed_equity > current:
        try:
            with open(PEAK_FILE, "w") as f:
                json.dump({
                    "peak_equity":    float(observed_equity),
                    "updated_at_iso": datetime.now(timezone.utc).isoformat(),
                    "updated_by":     source,
                }, f, indent=2)
            return observed_equity
        except Exception:
            return current
    return current
