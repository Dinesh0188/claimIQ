"""SQLite persistence for the portfolio dashboard.

Two tables, plain sqlite3, no ORM. The dashboard needs aggregates over a few
hundred rows; anything heavier here would be weight without benefit.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pandas as pd

from claimiq.config import ROOT
from claimiq.state import AuditResult

DB_PATH = ROOT / "data" / "claimiq.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    claim_id            TEXT PRIMARY KEY,
    audited_at          TEXT NOT NULL DEFAULT (datetime('now')),
    month               TEXT NOT NULL,
    diagnosis           TEXT,
    procedure           TEXT,
    gross_bill          REAL NOT NULL,
    settlement          REAL NOT NULL,
    patient_liability   REAL NOT NULL,
    hospital_writeoff   REAL NOT NULL,
    room_rent_deduction REAL NOT NULL DEFAULT 0,
    unmapped_count      INTEGER NOT NULL DEFAULT 0,
    doc_gap_count       INTEGER NOT NULL DEFAULT 0,
    corpus_version      TEXT,
    -- TRUE when this row came through the full LLM agent, FALSE for the
    -- deterministic-only path. The dashboard discloses the split rather than
    -- implying every row saw the model.
    ai_pipeline         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS findings (
    claim_id       TEXT NOT NULL,
    line_no        INTEGER NOT NULL,
    description    TEXT NOT NULL,
    head           TEXT,
    classification TEXT NOT NULL,
    bearer         TEXT NOT NULL,
    amount         REAL NOT NULL,
    deducted       REAL NOT NULL,
    cited_chunk_id TEXT,
    PRIMARY KEY (claim_id, line_no)
);

CREATE TABLE IF NOT EXISTS doc_gaps (
    claim_id    TEXT NOT NULL,
    document_id TEXT NOT NULL,
    name        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    PRIMARY KEY (claim_id, document_id)
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def _f(value: Decimal | float | None) -> float:
    return float(value or 0)


def save_audit(result: AuditResult, ai_pipeline: bool = False, month: str | None = None) -> None:
    typical = result.typical
    room_deduction = sum(
        (d.amount for d in typical.policy_deductions if d.step.startswith("room_rent")),
        Decimal("0"),
    )

    with connect() as conn:
        conn.execute("DELETE FROM claims   WHERE claim_id = ?", (result.claim_id,))
        conn.execute("DELETE FROM findings WHERE claim_id = ?", (result.claim_id,))
        conn.execute("DELETE FROM doc_gaps WHERE claim_id = ?", (result.claim_id,))

        conn.execute(
            """INSERT INTO claims (claim_id, month, diagnosis, procedure, gross_bill,
                   settlement, patient_liability, hospital_writeoff, room_rent_deduction,
                   unmapped_count, doc_gap_count, corpus_version, ai_pipeline)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                result.claim_id,
                month or "2026-07",
                result.diagnosis,
                result.procedure,
                _f(result.gross_bill),
                _f(typical.projected_settlement),
                _f(typical.patient_liability),
                _f(typical.hospital_writeoff),
                _f(room_deduction),
                result.unmapped_count,
                len(result.document_gaps),
                result.corpus_version,
                int(ai_pipeline),
            ),
        )
        conn.executemany(
            """INSERT INTO findings (claim_id, line_no, description, head, classification,
                   bearer, amount, deducted, cited_chunk_id) VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (
                    result.claim_id,
                    f.line_no,
                    f.description,
                    f.head,
                    f.classification,
                    f.bearer,
                    _f(f.amount),
                    _f(f.deducted_amount),
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
        return pd.read_sql_query("SELECT * FROM claims", conn)


def findings_df() -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql_query("SELECT * FROM findings", conn)


def doc_gaps_df() -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql_query("SELECT * FROM doc_gaps", conn)


def reset() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
