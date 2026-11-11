"""The reviewer-report line grammar, parsed in one place (#546): `<name>: <value>` lines such as `verdict:`, `round:`,
`blocking:` and `non-blocking:`. `worker-report` (the run log's `review` event) and `profile.py` (the review quality
floor) both read a reviewer's report, and each had its own regex for the same lines.

Stdlib only, Python 3.9+. A library: imported as `import reviewreport` (ADR-0003); `profile.py` runs under
`python3 -I` and appends its own directory to `sys.path` first, as it does for `common`.
"""

from __future__ import annotations

import re


def field(name: str, text: str):
    """The last `<name>: value` line of a reviewer's report, backticks and blanks trimmed; None when there is none."""
    found = re.findall(rf"^[ \t>*-]*{name}:[ \t]*`*(.*?)`*[ \t]*$", text, re.I | re.M)
    return found[-1].strip() if found and found[-1].strip() else None


def count(name: str, text: str):
    """The leading integer of the last `<name>:` line, or None when the line is absent or holds no number."""
    m = re.match(r"\d+", field(name, text) or "")
    return int(m.group()) if m else None
