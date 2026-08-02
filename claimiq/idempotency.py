"""Make a retried request return the first answer instead of doing the work twice.

Why this is not optional once there is a ledger.

A client that times out on `POST /api/audit` retries -- that is what every HTTP client
library does, correctly, because a timeout does not tell you whether the server acted.
Without idempotency the engine runs again, the portfolio row is overwritten (harmless,
it is the same answer) and **a second ledger entry is appended** (not harmless: the
compliance record now says the claim was audited twice, and a reconciliation against
it will double-count). The append-only store that makes retries traceable is exactly
the thing that makes them expensive.

So: the caller sends `Idempotency-Key`, and a repeat of the same key returns the stored
response without re-running anything.

Three rules, each of which exists because leaving it out produces a specific wrong
behaviour:

1. **The key is scoped to the tenant.** Two tenants picking the same UUID is
   vanishingly unlikely and the failure if they did would be one tenant reading the
   other's audit -- so the scope is not left to chance.
2. **The request body is fingerprinted alongside the key.** Reusing a key with a
   *different* body is a client bug, and returning the old answer would hide it
   behind a plausible response. That gets a 409.
3. **Entries expire.** A key store that grows forever is a slow leak, and a client
   retrying a request from last March is not retrying, it is asking again.

In-process for a single node would be wrong here -- a retry commonly lands on a
different replica than the original -- so this is SQLite, alongside the other stores.
Redis is the multi-node answer and the interface is narrow enough to swap.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from claimiq.config import ROOT

IDEMPOTENCY_PATH = ROOT / "data" / "idempotency.db"

# How long a key is honoured. A day is longer than any sane retry ladder and short
# enough that the table stays small; Stripe uses 24h for the same reasons.
TTL = timedelta(hours=24)

SCHEMA = """
CREATE TABLE IF NOT EXISTS keys (
    tenant       TEXT NOT NULL,
    key          TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status_code  INTEGER NOT NULL,
    body         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    PRIMARY KEY (tenant, key)
);

CREATE INDEX IF NOT EXISTS idx_keys_expiry ON keys(expires_at);
"""


def connect() -> sqlite3.Connection:
    IDEMPOTENCY_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(IDEMPOTENCY_PATH, timeout=10.0)
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def fingerprint(payload: object) -> str:
    """Content hash of a request body, insensitive to key order.

    Sorted, so a client that serialises its JSON differently between the original and
    the retry -- which is entirely normal, dict ordering is not a wire guarantee --
    does not get a spurious 409 telling it the body changed when it did not.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class KeyReused(Exception):
    """Same key, different body. The caller has a bug and should be told, not served."""


@dataclass(frozen=True)
class Replay:
    status_code: int
    body: dict


def lookup(tenant: str, key: str, request_hash: str) -> Replay | None:
    """The stored response for this key, or None if it is new.

    Raises `KeyReused` when the key is known but the body differs.
    """
    now = datetime.now(UTC)
    with connect() as conn:
        # Expired rows are removed on read rather than by a background sweep. There is
        # no scheduler in this process, and a table swept only when it is consulted is
        # a table that stays small exactly when it is being used.
        conn.execute("DELETE FROM keys WHERE expires_at < ?", (now.isoformat(),))
        row = conn.execute(
            "SELECT request_hash, status_code, body FROM keys WHERE tenant = ? AND key = ?",
            (tenant, key),
        ).fetchone()

    if row is None:
        return None
    if row["request_hash"] != request_hash:
        raise KeyReused(
            "this Idempotency-Key was already used with a different request body"
        )
    return Replay(row["status_code"], json.loads(row["body"]))


def remember(tenant: str, key: str, request_hash: str, status_code: int, body: dict) -> None:
    """Store a response against its key. Last write wins on a race, deliberately --
    two identical requests in flight produce two identical answers, so which one is
    kept does not matter."""
    now = datetime.now(UTC)
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO keys
                   (tenant, key, request_hash, status_code, body, created_at, expires_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                tenant,
                key,
                request_hash,
                status_code,
                json.dumps(body, default=str),
                now.isoformat(),
                (now + TTL).isoformat(),
            ),
        )


def reset() -> None:
    for path in (
        IDEMPOTENCY_PATH,
        IDEMPOTENCY_PATH.with_suffix(".db-wal"),
        IDEMPOTENCY_PATH.with_suffix(".db-shm"),
    ):
        path.unlink(missing_ok=True)
