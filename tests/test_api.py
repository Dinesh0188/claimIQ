"""The HTTP boundary, which nothing imported before.

The UI talks to the engine only over this surface, so an upload that returns the right
JSON with the wrong shape is invisible to every other test in the suite. Offline:
conftest forces AI_ENABLED=false, so only the deterministic text-layer path runs here.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claimiq import store, trace
from claimiq.api import app

ROOT = Path(__file__).parent.parent
GENERATED = ROOT / "data" / "generated"
CARDIAC_PDF = GENERATED / "cardiac.pdf"

needs_generated = pytest.mark.skipif(
    not CARDIAC_PDF.is_file(), reason="run scripts/gen_bill_pdf.py"
)


@pytest.fixture(autouse=True)
def isolated_writes(tmp_path, monkeypatch) -> None:
    """Keep the suite out of the repository's own data directory.

    Two separate sinks, and both had to be redirected. `/api/audit` persists to
    data/claimiq.db by default -- the portfolio the Leakage screen reports on. It also
    writes a per-claim trace file unconditionally, via `run.save()` in graph.audit(),
    which is not gated by `persist=False`; that left TEST-*.json lying in data/traces
    after every run, ready to be committed by accident.
    """
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(trace, "TRACE_DIR", tmp_path / "traces")


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def uploaded(client: TestClient) -> dict:
    with CARDIAC_PDF.open("rb") as handle:
        response = client.post(
            "/api/extract", files={"file": ("cardiac.pdf", handle, "application/pdf")}
        )
    assert response.status_code == 200, response.text
    return response.json()


# --- extraction -----------------------------------------------------------


@needs_generated
def test_extract_reads_a_native_pdf_without_a_model(uploaded: dict) -> None:
    assert uploaded["method"] == "text_layer"
    assert uploaded["line_items"]
    assert uploaded["document_id"] == "DOC-FINAL-BILL"


@needs_generated
def test_extract_returns_the_room_stay_it_read(uploaded: dict) -> None:
    """These fields crossed the boundary as permanent nulls before, so the UI could
    not use them and fell back to a sample claim's room stay instead."""
    stay = uploaded["room_stay"]

    assert stay is not None
    assert Decimal(str(stay["rate_per_day"])) == Decimal("12000")
    assert stay["days"] == 8
    assert uploaded["room_rate_per_day"] is not None
    assert uploaded["room_days"] == 8


def test_extract_rejects_an_unsupported_file_type(client: TestClient) -> None:
    response = client.post(
        "/api/extract", files={"file": ("notes.txt", b"hello", "text/plain")}
    )

    assert response.status_code == 400
    assert ".txt" in response.json()["detail"]


def test_extract_rejects_an_empty_upload(client: TestClient) -> None:
    response = client.post(
        "/api/extract", files={"file": ("empty.pdf", b"", "application/pdf")}
    )

    assert response.status_code == 400


# --- audit ----------------------------------------------------------------


@needs_generated
def test_audit_on_a_packet_built_from_an_upload(client: TestClient, uploaded: dict) -> None:
    """The shape the UI now sends: line items and room stay from the document, the
    clinical context from the form, and nothing at all from data/samples."""
    stay = uploaded["room_stay"]
    packet = {
        "claim_id": "TEST-UPLOAD-1",
        "context": {
            "claim_type": "cashless",
            "admission_date": "2026-03-01",
            "discharge_date": "2026-03-09",
            "primary_diagnosis": "Triple vessel coronary artery disease",
            "procedure_performed": "CABG",
            "documents_attached": ["DOC-FINAL-BILL"],
        },
        "policy": {
            "policy_id": "not stated",
            "sum_insured": "500000",
            "balance_sum_insured": "500000",
            "room_rent_cap_per_day": "6000",
            "copay_percent": "10",
            "deductible": "0",
            "procedure_sublimits": {},
        },
        "room_stay": stay,
        "line_items": uploaded["line_items"],
    }

    response = client.post("/api/audit", json=packet)
    assert response.status_code == 200, response.text
    result = response.json()

    typical = result["profiles"]["typical"]
    total = (
        Decimal(typical["projected_settlement"])
        + Decimal(typical["patient_liability"])
        + Decimal(typical["hospital_writeoff"])
    )
    assert total == Decimal(result["gross_bill"])
    assert result["diagnosis"] == "Triple vessel coronary artery disease"


@needs_generated
def test_the_room_stay_actually_drives_the_deduction(client: TestClient, uploaded: dict) -> None:
    """The regression guard. Auditing the same bill with a sample claim's room stay
    (Rs 5,500/day for 5 days, from billing_error.json) must not produce the same
    numbers as auditing it with the stay the bill states -- that equivalence was the
    bug, and it hid because the settlement still looked plausible."""
    base = {
        "claim_id": "TEST-ROOM",
        "context": {
            "claim_type": "cashless",
            "admission_date": "2026-03-01",
            "discharge_date": "2026-03-09",
            "primary_diagnosis": "Triple vessel coronary artery disease",
        },
        "policy": {
            "policy_id": "not stated",
            "sum_insured": "500000",
            "balance_sum_insured": "500000",
            "room_rent_cap_per_day": "6000",
            "copay_percent": "10",
        },
        "line_items": uploaded["line_items"],
    }

    from_bill = client.post(
        "/api/audit", json=base | {"room_stay": uploaded["room_stay"]}
    ).json()
    from_sample = client.post(
        "/api/audit",
        json=base
        | {
            "claim_id": "TEST-ROOM-2",
            "room_stay": {
                "room_category": "Single AC",
                "rate_per_day": "5500",
                "days": 5,
                "is_icu": False,
            },
        },
    ).json()

    assert (
        from_bill["profiles"]["typical"]["projected_settlement"]
        != from_sample["profiles"]["typical"]["projected_settlement"]
    )


# --- the defect this change removes ---------------------------------------


def test_the_upload_screen_never_loads_a_sample_packet() -> None:
    """Uploaded claims used to be built on top of data/samples/billing_error.json, so
    every one of them inherited that claim's dates, diagnosis, pre-auth and room stay.
    Easy to reintroduce by reaching for /api/samples again, so it is pinned here."""
    source = (ROOT / "ui" / "views" / "audit.py").read_text(encoding="utf-8")
    upload_section, _, sample_buttons = source.partition("SAMPLE_LABEL = {")

    assert "/api/samples/" not in upload_section
    # The sample buttons below are a separate, honest path and must keep working.
    assert "/api/samples/" in sample_buttons


def test_samples_on_disk_are_still_valid_packets() -> None:
    """The sample buttons remain the demo path, so they must keep parsing."""
    from claimiq.state import ClaimPacket

    for path in sorted((ROOT / "data" / "samples").glob("*.json")):
        ClaimPacket.model_validate(json.loads(path.read_text(encoding="utf-8")))
