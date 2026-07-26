"""Persistence: exact money, live foreign keys, and a migration that actually fires."""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from claimiq import store
from claimiq.graph import audit
from claimiq.state import ClaimPacket

SAMPLE = Path(__file__).parent.parent / "data" / "samples" / "cardiac.json"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    return tmp_path / "test.db"


def test_money_survives_a_round_trip_exactly(temp_db) -> None:
    """The engine is Decimal end to end; persistence must not quietly become float."""
    packet = ClaimPacket.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    result = audit(packet, persist=False)
    store.save_audit(result)

    row = store.claims_df().iloc[0]
    assert Decimal(str(row.gross_bill)) == result.gross_bill
    assert Decimal(str(row.settlement)) == result.typical.projected_settlement
    assert Decimal(str(row.hospital_writeoff)) == result.typical.hospital_writeoff


def test_money_is_stored_as_integer_paise(temp_db) -> None:
    packet = ClaimPacket.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    store.save_audit(audit(packet, persist=False))

    with store.connect() as conn:
        value = conn.execute("SELECT gross_bill_paise FROM claims").fetchone()[0]
    assert isinstance(value, int)
    assert value == 41_000_000  # Rs 4,10,000.00


def test_bucket_invariant_holds_after_persistence(temp_db) -> None:
    """The invariant that guards the waterfall must survive the storage round trip."""
    packet = ClaimPacket.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    store.save_audit(audit(packet, persist=False))

    row = store.claims_df().iloc[0]
    assert row.settlement + row.patient_liability + row.hospital_writeoff == pytest.approx(
        row.gross_bill
    )


def test_foreign_keys_are_enforced(temp_db) -> None:
    """A REFERENCES clause is decoration unless PRAGMA foreign_keys is on."""
    with store.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO findings (claim_id, line_no, description, classification, "
                "bearer, amount_paise, deducted_paise) VALUES (?,?,?,?,?,?,?)",
                ("NO-SUCH-CLAIM", 1, "orphan", "PAYABLE", "INSURER", 100, 0),
            )


def test_stale_database_is_migrated(temp_db) -> None:
    """user_version 0 means pre-versioning, not 'new'. Both must rebuild.

    Regression test: writing this check as `if version and version < SCHEMA_VERSION`
    treats 0 as falsy and skips migration on exactly the databases that need it.
    """
    temp_db.parent.mkdir(parents=True, exist_ok=True)
    old = sqlite3.connect(temp_db)
    old.executescript(
        "CREATE TABLE claims (claim_id TEXT PRIMARY KEY, gross_bill REAL);"
        "INSERT INTO claims VALUES ('OLD-1', 123.45);"
    )
    old.commit()
    old.close()

    with store.connect() as conn:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(claims)")}
        assert "gross_bill_paise" in columns, "stale schema was not rebuilt"
        assert "gross_bill" not in columns
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION


def test_indexes_exist(temp_db) -> None:
    with store.connect() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_findings_classification", "idx_findings_bearer", "idx_claims_month"} <= names
