"""Who is calling, what they may do, and how often.

Three concerns that are usually three libraries, kept together here because they
answer one question -- *is this request allowed to proceed* -- and because they share
the principal object that answers it.

**Authentication is off until an operator configures a key.** That is deliberate and
it is the single most important line in this module. The demo has to keep working for
someone who clones the repo, and a security layer that breaks `.\\start.ps1` is a
security layer people delete. What must never happen is authentication being off
*silently*, so `/health` states the posture and `describe()` exists to be shown.

Configuration is one environment variable, `CLAIMIQ_API_KEYS`, holding
`key:tenant:scope|scope` entries separated by commas::

    CLAIMIQ_API_KEYS=k_live_abc:apollo:audit|read,k_ro_xyz:apollo:read

Keys are compared with `hmac.compare_digest`. A plain `==` on a secret leaks its
prefix through timing, and while that is a marginal attack over the internet it is a
free thing to get right.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from threading import Lock

from claimiq.config import settings

# --- scopes ---------------------------------------------------------------
#
# Coarse on purpose. Fine-grained permissions are a liability when nobody has asked
# for them: they multiply the states an operator has to reason about, and the states
# they get wrong are the ones that grant too much.
#
#   read     GET anything -- results, rules, analytics, traces
#   audit    run the engine: single audits, batches, report PDFs
#   admin    change how the system behaves: provider switching
SCOPES = {"read", "audit", "admin"}

# What each route needs. Read is implied by the other two -- an operator who can run
# an audit can obviously see its result, and making them say so is bookkeeping.
IMPLIED: dict[str, set[str]] = {
    "audit": {"read"},
    "admin": {"read", "audit"},
}


def expand(scopes: set[str]) -> set[str]:
    out = set(scopes)
    for scope in scopes:
        out |= IMPLIED.get(scope, set())
    return out


@dataclass(frozen=True)
class Principal:
    """The caller, resolved. Never holds the key itself -- only a fingerprint of it.

    `key_id` is the first 12 hex characters of the key's SHA-256. It is enough to tell
    two keys apart in a log line and in the audit ledger, and not enough to replay one.
    Logging the key itself would put a live credential in every log aggregator the
    deployment ships to, which is how credentials leak in practice.
    """

    tenant: str
    key_id: str
    scopes: frozenset[str] = frozenset()
    # True for the implicit principal used when authentication is not configured.
    anonymous: bool = False

    def can(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def label(self) -> str:
        return f"{self.tenant}/{self.key_id}"


ANONYMOUS = Principal(
    tenant="local",
    key_id="anonymous",
    scopes=frozenset(expand({"admin"})),
    anonymous=True,
)


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class _Registry:
    """Parsed `CLAIMIQ_API_KEYS`. Empty `by_key` means authentication is disabled."""

    by_key: dict[str, Principal] = field(default_factory=dict)
    problems: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.by_key)


def _parse(raw: str) -> _Registry:
    keys: dict[str, Principal] = {}
    problems: list[str] = []

    for index, entry in enumerate(raw.split(","), start=1):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3:
            # Named by position rather than by content: the entry contains a secret,
            # and an error message is exactly the kind of string that gets pasted into
            # a ticket. Say which one is wrong, never what it says.
            problems.append(f"entry {index} is not key:tenant:scopes")
            continue

        key, tenant, scope_text = (p.strip() for p in parts)
        scopes = {s.strip() for s in scope_text.split("|") if s.strip()}
        unknown = scopes - SCOPES
        if not key or not tenant:
            problems.append(f"entry {index} has an empty key or tenant")
            continue
        if unknown:
            problems.append(f"entry {index} has unknown scope(s): {', '.join(sorted(unknown))}")
            continue
        if len(key) < 16:
            # Not a style rule. A short shared secret is brute-forceable over HTTP, and
            # the moment to say so is at configuration time rather than after.
            problems.append(f"entry {index} key is shorter than 16 characters")
            continue

        keys[key] = Principal(
            tenant=tenant, key_id=fingerprint(key), scopes=frozenset(expand(scopes))
        )

    return _Registry(by_key=keys, problems=tuple(problems))


@lru_cache(maxsize=1)
def registry() -> _Registry:
    return _parse(settings().api_keys_raw)


def auth_enabled() -> bool:
    return registry().enabled


def resolve(presented: str | None) -> Principal | None:
    """Map a presented key to its principal.

    Returns `ANONYMOUS` when authentication is not configured, and `None` when it is
    configured and the key does not match. The caller turns `None` into a 401.
    """
    reg = registry()
    if not reg.enabled:
        return ANONYMOUS
    if not presented:
        return None

    # Constant time, and constant *work*: iterate every key rather than returning on
    # the first hit, so the number of comparisons does not depend on which key matched.
    found: Principal | None = None
    for key, principal in reg.by_key.items():
        if hmac.compare_digest(key, presented):
            found = principal
    return found


def describe() -> dict:
    """Posture, for /health. Contains no secrets and is safe to expose unauthenticated."""
    reg = registry()
    return {
        "auth_enabled": reg.enabled,
        "principals": len(reg.by_key),
        "tenants": sorted({p.tenant for p in reg.by_key.values()}),
        "config_problems": list(reg.problems),
        "rate_limit_per_minute": settings().rate_limit_per_minute,
    }


# --- rate limiting --------------------------------------------------------


class SlidingWindowLimiter:
    """Per-principal request budget over a rolling 60 seconds.

    A sliding window rather than a fixed one because a fixed window lets a caller send
    two full budgets back to back across the boundary -- which is precisely the burst
    that knocks over a synchronous OCR endpoint.

    In-process, so it bounds one worker rather than a cluster. That is the honest scope
    of it: with several replicas behind a load balancer this becomes per-replica, and
    the real answer is Redis (see the roadmap in ARCHITECTURE.md). Stated here so the
    limit is not mistaken for a global one.
    """

    def __init__(self, per_minute: int, window_s: float = 60.0) -> None:
        self.per_minute = per_minute
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    def check(self, identity: str, now: float | None = None) -> tuple[bool, int, float]:
        """Record a hit. Returns (allowed, remaining, retry_after_seconds)."""
        if self.per_minute <= 0:
            return True, -1, 0.0

        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(identity, deque())
            cutoff = now - self.window_s
            while hits and hits[0] <= cutoff:
                hits.popleft()

            if len(hits) >= self.per_minute:
                # When the oldest hit ages out, one slot frees up.
                return False, 0, max(0.0, hits[0] + self.window_s - now)

            hits.append(now)
            return True, self.per_minute - len(hits), 0.0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


@lru_cache(maxsize=1)
def limiter() -> SlidingWindowLimiter:
    return SlidingWindowLimiter(settings().rate_limit_per_minute)
