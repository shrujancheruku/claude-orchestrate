import sys, time
sys.path.insert(0, "scripts")
import common, runlog, config, claims
from pathlib import Path
repo = Path("/Users/shrujan/code/claude-orchestrate")
p = runlog.log_path(repo, runlog.EVENTS_LOG)
t = time.time(); ev = list(common.read_jsonl(p)); print("parse", len(ev), round(time.time() - t, 3))
t = time.time(); config.Config.load(repo); print("config", round(time.time() - t, 3))
t = time.time(); claims._reports(repo); print("reports", round(time.time() - t, 3))
t = time.time(); claims._nudges(repo); print("nudges", round(time.time() - t, 3))
t = time.time(); common.worktrees(repo); print("worktrees", round(time.time() - t, 3))
