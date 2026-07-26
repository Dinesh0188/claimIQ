"""Per-node instrumentation. Written to JSONL and rendered in the UI Trace screen."""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from claimiq.config import ROOT

TRACE_DIR = ROOT / "data" / "traces"


class NodeTrace(BaseModel):
    node: str
    started_at: str
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    cache_hits: int = 0
    attempts: int = 1
    note: str = ""
    error: str = ""


class RunTrace(BaseModel):
    claim_id: str
    nodes: list[NodeTrace] = Field(default_factory=list)

    @property
    def total_ms(self) -> int:
        return sum(n.latency_ms for n in self.nodes)

    @property
    def total_tokens(self) -> int:
        return sum(n.prompt_tokens + n.completion_tokens for n in self.nodes)

    def save(self) -> None:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        path = TRACE_DIR / f"{self.claim_id}.json"
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")


# The graph is synchronous and single-claim, so a module-level current run is
# enough. No thread-locals for a problem that does not exist yet.
_current: RunTrace | None = None


def start_run(claim_id: str) -> RunTrace:
    global _current
    _current = RunTrace(claim_id=claim_id)
    return _current


def current() -> RunTrace | None:
    return _current


@contextmanager
def track(node: str, client=None):
    """Time a node and absorb any LLM usage it accumulated."""
    entry = NodeTrace(node=node, started_at=datetime.now(UTC).isoformat())
    before = len(client.calls) if client else 0
    started = time.perf_counter()
    try:
        yield entry
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        entry.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        entry.latency_ms = int((time.perf_counter() - started) * 1000)
        if client:
            new = client.calls[before:]
            entry.llm_calls = len(new)
            entry.cache_hits = sum(1 for c in new if c.cached)
            entry.prompt_tokens = sum(c.prompt_tokens for c in new)
            entry.completion_tokens = sum(c.completion_tokens for c in new)
            entry.attempts = max((c.attempts for c in new), default=1)
        if _current is not None:
            _current.nodes.append(entry)


def load_trace(claim_id: str) -> RunTrace | None:
    path = TRACE_DIR / f"{claim_id}.json"
    if not path.is_file():
        return None
    return RunTrace.model_validate_json(path.read_text(encoding="utf-8"))
