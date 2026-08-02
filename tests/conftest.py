"""Tests run with AI switched off, and write nowhere near the repository.

Deliberate: the suite must be fast, offline, deterministic and runnable by anyone
who clones the repo without a key. LLM quality is measured separately by
scripts/bench_classify.py and scripts/run_eval.py, where non-determinism is the
subject rather than a nuisance.
"""

import os

os.environ["AI_ENABLED"] = "false"

import pytest  # noqa: E402 - must follow the env var, which config reads at import


@pytest.fixture(autouse=True)
def no_repo_writes(tmp_path, monkeypatch):
    """Redirect every sink the engine writes to, for every test in the suite.

    This is autouse and lives in the root conftest rather than in one test module
    because the leak is not test-specific: `graph.audit()` writes three places, and
    any test that calls it -- including the engine and waterfall tests, which are
    about arithmetic and have no idea they are persisting anything -- was appending
    to the repository's own database, trace directory and audit ledger.

    The ledger made that visible. It is append-only by design, so instead of
    overwriting rows it accumulated one entry per test per run, and `git status`
    started showing a binary diff after every `pytest`. Fixing it only in the module
    that noticed would leave the same trap for the next sink anyone adds.
    """
    from claimiq import ledger, store, trace

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "claims.db")
    monkeypatch.setattr(trace, "TRACE_DIR", tmp_path / "traces")
    monkeypatch.setattr(ledger, "LEDGER_PATH", tmp_path / "ledger.db")
