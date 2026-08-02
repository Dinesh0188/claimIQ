"""Per-node instrumentation. Written to JSONL and rendered in the UI Trace screen."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
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
    # Whose run this was. Defaulted rather than required so the traces already
    # committed under data/traces -- written before tenants existed -- still parse and
    # still resolve, as the local demo's own.
    tenant: str = "local"
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


# A ContextVar, not a module global.
#
# The graph is synchronous per claim, which made a module global look sufficient --
# but FastAPI runs `def` endpoints in a threadpool, so two clients auditing at the
# same moment both wrote to the same `_current`. The second `start_run` discarded the
# first claim's partial trace, and every node that finished afterwards appended to the
# wrong claim. The failure is silent and asymmetric: the numbers are right, only the
# provenance is wrong, which is the worst kind of thing to be wrong in an auditor.
#
# Starlette copies the context per request before handing work to the threadpool, so
# a ContextVar isolates concurrent runs without any locking. Batch workers do the same
# thing explicitly (see jobs.py) by running each claim in its own copied context.
_current: ContextVar[RunTrace | None] = ContextVar("claimiq_trace_run", default=None)


def start_run(claim_id: str, tenant: str = "local") -> RunTrace:
    run = RunTrace(claim_id=claim_id, tenant=tenant)
    _current.set(run)
    return run


def current() -> RunTrace | None:
    return _current.get()


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
        run = _current.get()
        if run is not None:
            run.nodes.append(entry)


def load_trace(claim_id: str, tenant: str | None = None) -> RunTrace | None:
    """The stored trace, or None -- including when it belongs to someone else.

    A trace names the claim, its node timings and its token spend. Filed on disk by
    claim id alone, it was readable by anyone who could guess an id, which in a
    multi-tenant deployment is a leak the tenant-scoped database would not have
    allowed. Returning None rather than raising keeps a foreign trace and a missing
    one indistinguishable from outside.

    Filenames stay flat rather than moving under a per-tenant directory: the traces
    committed to data/traces are part of the demo, and a layout change would strand
    them for the sake of a check the field does just as well.
    """
    path = TRACE_DIR / f"{claim_id}.json"
    if not path.is_file():
        return None
    run = RunTrace.model_validate_json(path.read_text(encoding="utf-8"))
    if tenant is not None and run.tenant != tenant:
        return None
    return run
