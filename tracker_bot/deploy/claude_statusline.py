#!/usr/bin/env python3
"""Claude Code status line script: saves rate limits for the tracker bot and prints a short line.

Claude Code sends session JSON on stdin. `rate_limits` appears only for Pro/Max
subscribers and only after the first reply, so it is merged into the previous
snapshot instead of overwriting it with nothing.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HOME = Path(os.environ.get("CLAUDE_HOME") or Path.home() / ".claude")
SNAPSHOT = HOME / "tracker_snapshot.json"


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    now = int(time.time())
    try:
        snap = json.loads(SNAPSHOT.read_text())
    except (OSError, ValueError):
        snap = {}
    limits = data.get("rate_limits") or {}
    if limits:
        snap["rate_limits"] = limits
        snap["limits_captured_at"] = now
    snap["updated_at"] = now
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=HOME, prefix=".snapshot-")
        with os.fdopen(fd, "w") as fh:
            json.dump(snap, fh)
        os.replace(tmp, SNAPSHOT)  # atomic: the bot never reads a half-written file
    except OSError:
        pass  # never break the status line over a failed write

    parts = []
    for key, label in (("five_hour", "5ч"), ("seven_day", "7д")):
        pct = (snap.get("rate_limits") or {}).get(key, {}).get("used_percentage")
        if isinstance(pct, (int, float)):
            parts.append(f"{label} {pct:.0f}%")
    print(" · ".join(parts) if parts else "—")


if __name__ == "__main__":
    main()
