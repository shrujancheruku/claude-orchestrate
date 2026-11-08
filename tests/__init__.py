"""Makes `python3 -m unittest tests.test_x` work from the repo root.

The modules import `harness` and `transcripts` as bare names (a run from tests/ puts that directory on
sys.path); importing the package does the same for a run from the root.
"""

import sys
from pathlib import Path

_here = str(Path(__file__).resolve().parent)
if _here not in sys.path:
    sys.path.insert(0, _here)
