"""Append-only, hash-chained record of every determination the engine has made.

Why this exists, and why it is not just another table in `store.py`.

`store.py` holds the *current* portfolio: one row per claim, overwritten whenever that
claim is re-audited. That is the right shape for a dashboard and the wrong shape for
the question a regulator, an insurer or a hospital's own finance team eventually asks:
*on what basis did you tell us, in March, that this claim would settle at Rs 3.1 lakh?*
Answering it needs the input as it was, the corpus version as it was, and the answer as
it was -- none of which survives an overwrite.

So this is a second store with opposite rules. Nothing is ever updated or deleted. Each
entry carries the SHA-256 of the previous entry, so the sequence is tamper-evident: an
altered or removed row breaks the chain at that point and `verify_chain()` names the
sequence number where it broke. This is not a blockchain and does not pretend to be --
anyone with write access to the file can rewrite the whole chain from the break onward.
What it buys is that they cannot do it *quietly*, and combined with an offsite copy of
the latest head hash it becomes genuinely hard.

What is recorded, and what deliberately is not:

  recorded   claim id, tenant, principal fingerprint, corpus version, engine strategy,
             verdict, the three money figures, counts of findings by class, and a
             digest of the input packet
  not        the packet itself, patient names, diagnoses in free text

The digest is the compromise. It proves *which* input produced this answer -- re-hash
the packet you have and compare -- without the ledger becoming a second uncontrolled
copy of the clinical record. A compliance store that accumulates PHI is a liability,
and the entries that make it useful do not need any.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from claimiq.config import ROOT, settings
from claimiq.money import to_paise
from claimiq.state import AuditResult, ClaimPacket

LEDGER_PATH = ROOT / "data" / "ledger.db"

GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at    TEXT NOT NULL,
    tenant         TEXT NOT NULL,
    key_id         TEXT NOT NULL,
    request_id     TEXT NOT NULL DEFAULT '',
    claim_id       TEXT NOT NULL,
    packet_digest  TEXT NOT NULL,
    corpus_version TEXT NOT NULL,
    strategy       TEXT NOT NULL,
    verdict        TEXT NOT NULL,
    gross_paise       INTEGER NOT NULL,
    settlement_paise  INTEGER NOT NULL,
    patient_paise     INTEGER NOT NULL,
    hospital_paise    INTEGER NOT NULL,
    finding_counts TEXT NOT NULL DEFAULT '{}',
    verify_passed  INTEGER NOT NULL DEFAULT 1,
    prev_hash      TEXT NOT NULL,
    entry_hash     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entries_claim  ON entries(claim_id);
CREATE INDEX IF NOT EXISTS idx_entries_tenant ON entries(tenant);
"""


def connect() -> sqlite3.Connection:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(LEDGER_PATH, timeout=10.0)
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def packet_digest(packet: ClaimPacket) -> str:
    """Stable content hash of the input.

    `model_dump_json` with sorted keys, so two structurally identical packets hash the
    same however their JSON happened to be ordered on the wire. Without the sort the
    digest would record the serialisation rather than the claim, and re-verifying an
    entry a year later would fail for no reason anyone could act on.
    """
    payload = json.loads(packet.model_dump_json())
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# The exact fields the chain covers, in order. Written out rather than derived from the
# row so that adding a column later cannot silently change every historical hash --
# which would invalidate the entire ledger on a deploy that touched the schema.
CHAINED_FIELDS = (
    "recorded_at", "tenant", "key_id", "claim_id", "packet_digest", "corpus_version",
    "strategy", "verdict", "gross_paise", "settlement_paise", "patient_paise",
    "hospital_paise", "finding_counts", "verify_passed", "prev_hash",
)


def entry_hash(row: dict) -> str:
    material = "|".join(str(row[f]) for f in CHAINED_FIELDS)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChainStatus:
    ok: bool
    entries: int
    head: str
    broken_at: int | None = None
    reason: str = ""


def head() -> tuple[int, str]:
    """(seq, hash) of the newest entry, or (0, GENESIS) for an empty ledger."""
    with connect() as conn:
        row = conn.execute("SELECT seq, entry_hash FROM entries ORDER BY seq DESC LIMIT 1").fetchone()
    return (row["seq"], row["entry_hash"]) if row else (0, GENESIS)


def record(
    result: AuditResult,
    packet: ClaimPacket,
    *,
    tenant: str = "local",
    key_id: str = "anonymous",
    request_id: str = "",
) -> str:
    """Append one entry. Returns its hash, which is also the new chain head."""
    if not settings().ledger_enabled:
        return ""

    typical = result.typical
    counts: dict[str, int] = {}
    for finding in result.findings:
        counts[finding.classification] = counts.get(finding.classification, 0) + 1

    row = {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tenant": tenant,
        "key_id": key_id,
        "request_id": request_id,
        "claim_id": result.claim_id,
        "packet_digest": packet_digest(packet),
        "corpus_version": result.corpus_version,
        "strategy": result.strategy,
        "verdict": result.verdict,
        "gross_paise": to_paise(result.gross_bill),
        "settlement_paise": to_paise(typical.projected_settlement),
        "patient_paise": to_paise(typical.patient_liability),
        "hospital_paise": to_paise(typical.hospital_writeoff),
        "finding_counts": json.dumps(counts, sort_keys=True),
        "verify_passed": int(result.verify_passed),
    }

    # Read the head and append inside one transaction. Two batch workers finishing at
    # the same instant would otherwise both read head N and both write prev_hash=N,
    # forking the chain -- which verify_chain() would report as tampering when it was
    # only a race. BEGIN IMMEDIATE takes the write lock up front rather than on the
    # INSERT, so the second worker waits instead of reading a stale head.
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute(
            "SELECT entry_hash FROM entries ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        row["prev_hash"] = previous["entry_hash"] if previous else GENESIS
        digest = entry_hash(row)
        conn.execute(
            f"""INSERT INTO entries ({', '.join(row)}, entry_hash)
                VALUES ({', '.join('?' * len(row))}, ?)""",
            (*row.values(), digest),
        )
        conn.commit()

    return digest


def verify_chain() -> ChainStatus:
    """Walk the whole chain and report the first entry that does not hold up."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM entries ORDER BY seq ASC").fetchall()

    expected_prev = GENESIS
    for row in rows:
        data = dict(row)
        if data["prev_hash"] != expected_prev:
            return ChainStatus(
                False, len(rows), expected_prev, data["seq"],
                "prev_hash does not match the preceding entry -- an entry was removed or reordered",
            )
        if entry_hash(data) != data["entry_hash"]:
            return ChainStatus(
                False, len(rows), expected_prev, data["seq"],
                "entry_hash does not match its contents -- this row was edited after it was written",
            )
        expected_prev = data["entry_hash"]

    return ChainStatus(True, len(rows), expected_prev)


def history(claim_id: str | None = None, tenant: str | None = None, limit: int = 100) -> list[dict]:
    """Entries newest first, optionally narrowed to one claim and always to one tenant.

    `tenant` is not an optional filter in the security sense -- the API always passes
    the caller's own tenant when authentication is on. It is None only for the
    unauthenticated local demo, where there is one tenant by definition.
    """
    clauses, params = [], []
    if claim_id:
        clauses.append("claim_id = ?")
        params.append(claim_id)
    if tenant:
        clauses.append("tenant = ?")
        params.append(tenant)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM entries {where} ORDER BY seq DESC LIMIT ?", (*params, int(limit))
        ).fetchall()

    return [_readable(dict(r)) for r in rows]


def _readable(row: dict) -> dict:
    """Paise back to rupee strings, counts back to a dict. Strings, not floats -- the
    whole system keeps money out of binary floating point and the read path is not
    the place to give that up."""
    out = dict(row)
    for column in ("gross", "settlement", "patient", "hospital"):
        out[column] = str(Decimal(out.pop(f"{column}_paise")) / 100)
    out["finding_counts"] = json.loads(out["finding_counts"])
    out["verify_passed"] = bool(out["verify_passed"])
    return out


def reset() -> None:
    for path in (LEDGER_PATH, LEDGER_PATH.with_suffix(".db-wal"), LEDGER_PATH.with_suffix(".db-shm")):
        path.unlink(missing_ok=True)
