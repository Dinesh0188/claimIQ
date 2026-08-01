"""The ingestion half of the pipeline, which had no coverage at all.

The engine was well tested and the reader was not, which is the wrong way round: a
waterfall that is exactly right about the wrong room rate is still exactly wrong. The
room stay in particular used to be inherited from a sample claim rather than read off
the bill, so these tests pin the derivation that replaced it.

Everything here is offline -- conftest forces AI_ENABLED=false and none of these paths
touch a model.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from claimiq.nodes.extract import (
    DOCUMENT_SIGNATURES,
    _row_to_item,
    detect_document_type,
    extract_from_text_layer,
    room_stay_from_items,
)
from claimiq.state import BillLineItem

GENERATED = Path(__file__).parent.parent / "data" / "generated"


def item(description: str, head: str = "OTHER", **kwargs) -> BillLineItem:
    defaults = {"line_no": 1, "amount": Decimal("1000")}
    return BillLineItem(description=description, head=head, **(defaults | kwargs))


# --- room stay ------------------------------------------------------------


def test_room_stay_reads_rate_and_days_from_the_room_row() -> None:
    stay = room_stay_from_items(
        [
            item("Room rent - Single AC", "ROOM", quantity=Decimal("8"),
                 unit_rate=Decimal("12000"), amount=Decimal("96000")),
            item("Surgeon fee", "PROCEDURE", amount=Decimal("50000")),
        ]
    )

    assert stay is not None
    assert stay.rate_per_day == Decimal("12000")
    assert stay.days == 8
    assert stay.room_category == "Room rent - Single AC"
    assert stay.is_icu is False


def test_room_stay_is_none_without_a_room_row() -> None:
    """The bill cannot say what the stay was, so nothing may be invented."""
    assert room_stay_from_items([item("Surgeon fee", "PROCEDURE")]) is None
    assert room_stay_from_items([]) is None


def test_room_stay_falls_back_to_the_row_total_when_no_rate_is_printed() -> None:
    stay = room_stay_from_items(
        [item("Ward bed", "ROOM", quantity=Decimal("4"), unit_rate=Decimal("0"),
              amount=Decimal("20000"))]
    )

    assert stay is not None
    assert stay.rate_per_day == Decimal("5000")
    assert stay.days == 4


def test_room_stay_takes_the_dearest_row_and_sums_the_days() -> None:
    """A ward-then-ICU stay bills two rows. Averaging would understate the peak rate,
    and the peak rate is the one the cap is tested against."""
    stay = room_stay_from_items(
        [
            item("General ward", "ROOM", quantity=Decimal("3"),
                 unit_rate=Decimal("4000"), amount=Decimal("12000")),
            item("ICU bed charges", "ROOM", quantity=Decimal("2"),
                 unit_rate=Decimal("15000"), amount=Decimal("30000")),
        ]
    )

    assert stay is not None
    assert stay.rate_per_day == Decimal("15000")
    assert stay.days == 5
    assert stay.is_icu is True


@pytest.mark.parametrize(
    "description", ["ICU bed", "Intensive Care Unit", "HDU charges", "Critical care bed"]
)
def test_icu_is_detected_from_the_description(description: str) -> None:
    """is_icu selects the ICU cap instead of the room cap, which moves the settlement."""
    stay = room_stay_from_items(
        [item(description, "ROOM", quantity=Decimal("2"), unit_rate=Decimal("9000"))]
    )

    assert stay is not None and stay.is_icu is True


def test_room_stay_rejects_a_zero_value_room_row() -> None:
    assert room_stay_from_items(
        [item("Room rent", "ROOM", quantity=Decimal("0"), unit_rate=Decimal("0"),
              amount=Decimal("0"))]
    ) is None


# --- against a real generated bill ----------------------------------------


@pytest.mark.skipif(not (GENERATED / "cardiac.pdf").is_file(), reason="run scripts/gen_bill_pdf.py")
def test_room_stay_off_the_generated_cardiac_bill() -> None:
    """End to end on the file a user would actually upload: the parsed rows must
    reproduce the cardiac sample's stay of Rs 12,000/day for 8 days."""
    items = extract_from_text_layer(GENERATED / "cardiac.pdf")
    assert len(items) >= 3, "text-layer parse found no table"

    stay = room_stay_from_items(items)
    assert stay is not None
    assert stay.rate_per_day == Decimal("12000")
    assert stay.days == 8


# --- document classification ----------------------------------------------


@pytest.mark.parametrize(
    ("doc_id", "keyword"),
    [(doc_id, keywords[0]) for doc_id, _, keywords in DOCUMENT_SIGNATURES],
    ids=[doc_id for doc_id, _, _ in DOCUMENT_SIGNATURES],
)
def test_every_signature_identifies_its_own_document(doc_id: str, keyword: str) -> None:
    found, label = detect_document_type(f"Apollo Hospitals\n{keyword}\npage 1 of 2")

    assert found == doc_id
    assert label


def test_unrecognised_text_is_not_guessed_at() -> None:
    found, label = detect_document_type("a letter about something else entirely")

    assert found is None
    assert label == "Unrecognised"


# --- row parsing ----------------------------------------------------------


def test_row_to_item_parses_a_charge_row() -> None:
    parsed = _row_to_item(["1", "Room rent", "Accommodation", "8", "12000", "96000"], 1)

    assert parsed is not None
    assert parsed.head == "ROOM"
    assert parsed.amount == Decimal("96000")
    assert parsed.quantity == Decimal("8")


@pytest.mark.parametrize(
    "row",
    [
        ["Sr", "Description", "Dept", "Qty", "Rate", "Amount"],  # header
        ["", "TOTAL", "", "", "", "410000"],                     # total, no serial
        ["1", "", "Accommodation", "8", "12000", "96000"],       # no description
        ["1", "Room rent", "Accommodation", "8", "12000", "0"],  # zero amount
        ["1", "Room rent", "8", "96000"],                        # too few cells
    ],
    ids=["header", "total", "no-description", "zero-amount", "short-row"],
)
def test_row_to_item_rejects_non_charge_rows(row: list[str]) -> None:
    assert _row_to_item(row, 1) is None
