"""Tests run with AI switched off.

Deliberate: the suite must be fast, offline, deterministic and runnable by anyone
who clones the repo without a key. LLM quality is measured separately by
scripts/bench_classify.py and scripts/run_eval.py, where non-determinism is the
subject rather than a nuisance.
"""

import os

os.environ["AI_ENABLED"] = "false"
