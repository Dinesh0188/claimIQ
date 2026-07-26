"""SQLite persistence for the portfolio dashboard.

Three tables, plain sqlite3, no ORM. The dashboard aggregates a few hundred rows;
anything heavier here would be weight without benefit. See DECISIONS.md for why this
is not Postgres.

**Money is stored as integer paise, never REAL.** The engine is Decimal end to end and
the README makes a point of it, so persisting to a float would throw that away at the
last boundary. At this row count the drift would be invisible -- roughly 1e-10 on an
aggregate -- so this is a consistency fix rather than a live bug, but "money is never
float" should be true everywhere or it should not be claimed.

Rupee-denominated VIEWs sit on top. Reporting and the text-to-SQL feature read those,
so generated SQL keeps rupee semantics and answers come back as 24300 rather than
2430000, while the stored value stays exact.
"""

from __future__ import annotations

import sqlite3
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

from claimiq.config import ROOT
from claimiq.state import AuditResult

DB_PATH = ROOT / "data" / "claimiq.db"

# Bumped when the schema changes shape. A stale database is dropped and rebuilt rather
# than migrated: every row is reproducible from data/generated/portfolio via seed_db.py,
# so a migration path would be ceremony for data that is regenerated in 30 seconds.
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    claim_id                  TEXT PRIMARY KEY,
    audited_at                TEXT NOT NULL DEFAULT (datetime('now')),
    month                     TEXT NOT NULL,
    diagnosis                 TEXT,
    procedure                 TEXT,
    gross_bill_paise          INTEGER NOT NULL,
    settlement_paise          INTEGER NOT NULL,
    patient_liability_paise   INTEGER NOT NULL,
    hospital_writeoff_paise   INTEGER NOT NULL,
    room_rent_deduction_paise INTEGER NOT NULL DEFAULT 0,
    unmapped_count            INTEGER NOT NULL DEFAULT 0,
    doc_gap_count             INTEGER NOT NULL DEFAULT 0,
    corpus_version            TEXT,
    -- TRUE when this row came through the full LLM agent, FALSE for the
    -- deterministic-only path. The dashboard discloses the split rather than
    -- implying every row saw the model.
    ai_pipeline               INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS findings (
    claim_id       TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    line_no        INTEGER NOT NULL,
    description    TEXT NOT NULL,
    head           TEXT,
    classification TEXT NOT NULL,
    bearer         TEXT NOT NULL,
    amount_paise   INTEGER NOT NULL,
    deducted_paise INTEGER NOT NULL,
    cited_chunk_id TEXT,
    PRIMARY KEY (claim_id, line_no)
);

CREATE TABLE IF NOT EXISTS doc_gaps (
    claim_id    TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    name        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    PRIMARY KEY (claim_id, document_id)
);

-- Precautionary at 302 rows, where SQLite scans the whole table faster than it can
-- consult an index. They earn their place from roughly 10k findings onward, which is
-- ~500 claims -- close enough to the demo size to be worth having already.
CREATE INDEX IF NOT EXISTS idx_findings_classification ON findings(classification);
CREATE INDEX IF NOT EXISTS idx_findings_bearer         ON findings(bearer);
CREATE INDEX IF NOT EXISTS idx_claims_month            ON claims(month);
CREATE INDEX IF NOT EXISTS idx_doc_gaps_severity       ON doc_gaps(severity);

-- Rupee views. Storage stays exact; readers get natural units.
CREATE VIEW IF NOT EXISTS v_claims AS
SELECT claim_id, audited_at, month, diagnosis, procedure,
       gross_bill_paise          / 100.0 AS gross_bill,
       settlement_paise          / 100.0 AS settlement,
       patient_liability_paise   / 100.0 AS patient_liability,
       hospital_writeoff_paise   / 100.0 AS hospital_writeoff,
       room_rent_deduction_paise / 100.0 AS room_rent_deduction,
       unmapped_count, doc_gap_count, corpus_version, ai_pipeline
FROM claims;

CREATE VIEW IF NOT EXISTS v_findings AS
SELECT claim_id, line_no, description, head, classification, bearer,
       amount_paise   / 100.0 AS amount,
       deducted_paise / 100.0 AS deducted,
       cited_chunk_id
FROM findings;
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    # SQLite silently ignores REFERENCES clauses unless this is on, per connection.
    # Declaring a foreign key without it is decoration.
    conn.execute("PRAGMA foreign_keys = ON")

    # 0 means either a brand-new file or a pre-versioning database -- both need the
    # rebuild, so this must not be written as `if version and ...`. Treating 0 as
    # falsy skips the migration on precisely the stale databases that require it,
    # and the failure surfaces later as an OperationalError on an INSERT.
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        conn.executescript(
            "DROP VIEW IF EXISTS v_findings; DROP VIEW IF EXISTS v_claims;"
            "DROP TABLE IF EXISTS doc_gaps; DROP TABLE IF EXISTS findings;"
            "DROP TABLE IF EXISTS claims;"
        )

    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


def _paise(value: Decimal | float | None) -> int:
    """Rupees to exact integer paise. Half-up, matching the waterfall's rounding."""
    if value is None:
        return 0
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def save_audit(result: AuditResult, ai_pipeline: bool = False, month: str | None = None) -> None:
    typical = result.typical
    room_deduction = sum(
        (d.amount for d in typical.policy_deductions if d.step.startswith("room_rent")),
        Decimal("0"),
    )

    with connect() as conn:
        # Children go first: ON DELETE CASCADE only fires on a parent delete, and the
        # parent row may not exist yet on a first insert.
        conn.execute("DELETE FROM findings WHERE claim_id = ?", (result.claim_id,))
        conn.execute("DELETE FROM doc_gaps WHERE claim_id = ?", (result.claim_id,))
        conn.execute("DELETE FROM claims   WHERE claim_id = ?", (result.claim_id,))

        conn.execute(
            """INSERT INTO claims (claim_id, month, diagnosis, procedure, gross_bill_paise,
                   settlement_paise, patient_liability_paise, hospital_writeoff_paise,
                   room_rent_deduction_paise, unmapped_count, doc_gap_count,
                   corpus_version, ai_pipeline)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                result.claim_id,
                month or "2026-07",
                result.diagnosis,
                result.procedure,
                _paise(result.gross_bill),
                _paise(typical.projected_settlement),
                _paise(typical.patient_liability),
                _paise(typical.hospital_writeoff),
                _paise(room_deduction),
                result.unmapped_count,
                len(result.document_gaps),
                result.corpus_version,
                int(ai_pipeline),
            ),
        )
        conn.executemany(
            """INSERT INTO findings (claim_id, line_no, description, head, classification,
                   bearer, amount_paise, deducted_paise, cited_chunk_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (
                    result.claim_id,
                    f.line_no,
                    f.description,
                    f.head,
                    f.classification,
                    f.bearer,
                    _paise(f.amount),
                    _paise(f.deducted_amount),
                    f.cited_chunk_id,
                )
                for f in result.findings
            ],
        )
        conn.executemany(
            "INSERT INTO doc_gaps (claim_id, document_id, name, severity) VALUES (?,?,?,?)",
            [(result.claim_id, g.document_id, g.name, g.severity) for g in result.document_gaps],
        )


def claims_df() -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql_query("SELECT * FROM v_claims", conn)


def findings_df() -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql_query("SELECT * FROM v_findings", conn)


def doc_gaps_df() -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql_query("SELECT * FROM doc_gaps", conn)


def reset() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
