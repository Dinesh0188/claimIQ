"""Data retention as code, and what erasure means against an append-only ledger.

Two questions that look like one and are not.

**Retention** is "this data has aged out". Claim packets, extracted line items,
diagnoses -- clinical detail with a shelf life. Deleting it on a schedule is
straightforward and the only real decision is what the schedule is.

**Erasure** is "this person has asked to be forgotten", and it collides head-on with
the ledger. The ledger's value is that nothing in it changes; a deletion request says
something in it must. Both cannot be true, so the design has to choose which one bends
and say so out loud.

The choice here: **the chain is never broken, and the personal data was never in it.**
That is why `ledger.py` stores a digest of the packet rather than the packet, and
counts of findings rather than their descriptions. An erasure request deletes the
clinical record from `claimiq.db` and appends a *tombstone* entry to the ledger saying
an erasure happened, when, and for which claim. What survives is the arithmetic and
the fact of the decision -- which is what an auditor needs and what a regulator's
right-to-erasure does not reach, because it is no longer personal data once the
identifying content is gone.

The honest limitation, stated rather than buried: `claim_id` itself remains in the
ledger. If a hospital's claim ids encode patient identity -- and some do -- the digest
design does not save you and the id must be pseudonymised at ingestion. There is a
`pseudonymise` hook below for exactly that, unused by default because inventing a
mapping nobody asked for would be worse than naming the gap.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from claimiq import ledger, store
from claimiq.observability import log

# Defaults, and they are conservative in the direction that matters: keeping clinical
# detail longer than necessary is a liability, so the default is the shorter end of
# what an Indian insurer's audit cycle actually needs. An operator with a contractual
# obligation to keep more raises it deliberately.
DEFAULT_CLAIM_RETENTION_DAYS = 365 * 2
DEFAULT_TRACE_RETENTION_DAYS = 90


@dataclass(frozen=True)
class PurgeReport:
    claims_deleted: int
    findings_deleted: int
    gaps_deleted: int
    traces_deleted: int
    cutoff: str
    dry_run: bool

    def as_dict(self) -> dict:
        return {**self.__dict__}


def purge_expired(
    tenant: str | None = None,
    claim_days: int = DEFAULT_CLAIM_RETENTION_DAYS,
    trace_days: int = DEFAULT_TRACE_RETENTION_DAYS,
    dry_run: bool = True,
) -> PurgeReport:
    """Delete claims and traces older than the retention window.

    `dry_run=True` is the default, and that is not timidity. A retention job is
    irreversible by definition, it is usually run first by someone verifying a policy
    rather than enforcing it, and the difference between the two is one flag. Making
    the destructive mode the one you have to ask for costs a keystroke and prevents
    the class of incident where a mistyped `days` argument empties the table.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=claim_days)).strftime("%Y-%m-%d")
    trace_cutoff = datetime.now(UTC) - timedelta(days=trace_days)

    where = "audited_at < ?"
    params: list = [cutoff]
    if tenant is not None:
        where += " AND tenant = ?"
        params.append(tenant)

    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        doomed = conn.execute(
            f"SELECT tenant, claim_id FROM claims WHERE {where}", params
        ).fetchall()

        findings = gaps = 0
        if not dry_run:
            for row in doomed:
                scope = (row["tenant"], row["claim_id"])
                findings += conn.execute(
                    "DELETE FROM findings WHERE tenant = ? AND claim_id = ?", scope
                ).rowcount
                gaps += conn.execute(
                    "DELETE FROM doc_gaps WHERE tenant = ? AND claim_id = ?", scope
                ).rowcount
                conn.execute(
                    "DELETE FROM claims WHERE tenant = ? AND claim_id = ?", scope
                )

    traces = _purge_traces(trace_cutoff, tenant, dry_run)

    report = PurgeReport(
        claims_deleted=len(doomed),
        findings_deleted=findings,
        gaps_deleted=gaps,
        traces_deleted=traces,
        cutoff=cutoff,
        dry_run=dry_run,
    )
    log("retention.purge", **report.as_dict(), tenant=tenant or "all")
    return report


def _purge_traces(cutoff: datetime, tenant: str | None, dry_run: bool) -> int:
    """Traces are files, not rows, so they age out on their own schedule.

    Their retention is shorter than the claims': a trace holds node latencies and token
    counts, which are worth having while someone is debugging last week and worth
    nothing eighteen months later. Read the tenant out of the file rather than the
    filename, because the filename is the claim id and always was.
    """
    from claimiq import trace as trace_module

    directory = trace_module.TRACE_DIR
    if not directory.is_dir():
        return 0

    removed = 0
    for path in directory.glob("*.json"):
        try:
            run = trace_module.RunTrace.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt trace is not worth failing a purge
            continue
        if tenant is not None and run.tenant != tenant:
            continue
        if datetime.fromtimestamp(path.stat().st_mtime, UTC) >= cutoff:
            continue
        removed += 1
        if not dry_run:
            path.unlink(missing_ok=True)
    return removed


# --- erasure ---------------------------------------------------------------


def erase_claim(claim_id: str, tenant: str, reason: str = "erasure request") -> dict:
    """Remove one claim's clinical record, and record that it happened.

    Deliberately NOT a ledger deletion. The chain stays intact and gains an entry
    saying an erasure occurred -- so the history remains verifiable and the thing the
    request was about is gone. A system that answered an erasure request by silently
    breaking its own audit trail would have destroyed the evidence that it complied.
    """
    with store.connect() as conn:
        scope = (tenant, claim_id)
        existed = conn.execute(
            "SELECT 1 FROM claims WHERE tenant = ? AND claim_id = ?", scope
        ).fetchone()
        if existed is None:
            return {"erased": False, "reason": f"no stored claim {claim_id!r}"}

        conn.execute("DELETE FROM findings WHERE tenant = ? AND claim_id = ?", scope)
        conn.execute("DELETE FROM doc_gaps WHERE tenant = ? AND claim_id = ?", scope)
        conn.execute("DELETE FROM claims   WHERE tenant = ? AND claim_id = ?", scope)

    from claimiq import trace as trace_module

    (trace_module.TRACE_DIR / f"{claim_id}.json").unlink(missing_ok=True)

    tombstone = _append_tombstone(claim_id, tenant, reason)
    log("retention.erased", claim_id=claim_id, tenant=tenant, reason=reason)
    return {"erased": True, "claim_id": claim_id, "tombstone": tombstone}


def _append_tombstone(claim_id: str, tenant: str, reason: str) -> str:
    """A ledger entry marking an erasure, chained like any other.

    Written through the ledger's own connection and hashing so it is indistinguishable
    from a normal entry to `verify_chain` -- which is the point. A tombstone that the
    verifier had to special-case would be a second code path through the one function
    whose correctness the whole design rests on.
    """
    row = {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tenant": tenant,
        "key_id": "retention",
        "request_id": "",
        "claim_id": claim_id,
        "packet_digest": "",
        "corpus_version": "",
        "strategy": "erasure",
        "verdict": "ERASED",
        "gross_paise": 0,
        "settlement_paise": 0,
        "patient_paise": 0,
        "hospital_paise": 0,
        "finding_counts": json.dumps({"reason": reason}, sort_keys=True),
        "verify_passed": 1,
    }

    with ledger.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute(
            "SELECT entry_hash FROM entries ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        row["prev_hash"] = previous["entry_hash"] if previous else ledger.GENESIS
        digest = ledger.entry_hash(row)
        conn.execute(
            f"""INSERT INTO entries ({', '.join(row)}, entry_hash)
                VALUES ({', '.join('?' * len(row))}, ?)""",
            (*row.values(), digest),
        )
        conn.commit()
    return digest


def pseudonymise(claim_id: str, salt: str) -> str:
    """A stable surrogate for a claim id that encodes patient identity.

    Unused by default and present because the gap is real: some hospitals number claims
    in a way that identifies the patient, and for those the ledger's digest design does
    not protect anything -- the identifier is right there in the clear.

    Keyed rather than plain SHA-256. An unsalted hash of a short structured identifier
    is reversible by enumeration in seconds, which is the standard way pseudonymisation
    turns out not to be pseudonymisation. The salt must be stored somewhere the claims
    database is not, or this is theatre.
    """
    return hmac.new(salt.encode("utf-8"), claim_id.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
