"""Status-line tap: log plan usage for /orchestrate:run --profile.

Pipe the status line's JSON input through it; it prints nothing:

    echo "$input" | python3 -I ~/.claude/orchestrate/usage-tap.py

Claude Code passes `rate_limits` to the status line only for subscribers, so
this is the one place plan headroom is visible. Appends a line to
`~/.claude/orchestrate/usage.jsonl` only when a percentage or reset changes.
Never fails the status line: any error is swallowed.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    limits = json.load(sys.stdin).get("rate_limits") or {}
    five, week = limits.get("five_hour") or {}, limits.get("seven_day") or {}
    row = {
        "five_hour": five.get("used_percentage"),
        "five_hour_resets_at": five.get("resets_at"),
        "seven_day": week.get("used_percentage"),
        "seven_day_resets_at": week.get("resets_at"),
    }
    if row["five_hour"] is None and row["seven_day"] is None:
        return
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    log = Path(os.environ.get("ORCHESTRATE_USAGE_LOG") or base / "orchestrate" / "usage.jsonl")
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log, "rb") as f:
            f.seek(max(f.seek(0, 2) - 512, 0))
            last = json.loads(f.read().splitlines()[-1])
        if all(last.get(k) == v for k, v in row.items()):
            return
    except (OSError, ValueError, IndexError):
        pass
    row = {"ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), **row}
    with open(log, "a") as f:
        f.write(json.dumps(row) + "\n")


try:
    main()
except Exception:
    pass
