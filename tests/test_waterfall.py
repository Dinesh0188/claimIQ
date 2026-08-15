"""The invariant that catches real waterfall bugs.

Every rupee on the bill ends up in exactly one of three buckets. If they do not sum
to the gross bill, a deduction has been double-counted or attributed to the wrong
party -- the most common way this kind of engine goes quietly wrong.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from claimiq.graph import audit
from claimiq.nodes.classify import classify_node
from claimiq.state import (
    BillLineItem,
    ClaimContext,
    ClaimPacket,
    ClaimState,
    PolicyTerms,
    RoomStay,
)
from claimiq.tools.waterfall import PROFILES, compute_waterfall, simulate_room_downgrade

SAMPLES = sorted((Path(__file__).parent.parent / "data" / "samples").glob("*.json"))


def load(path: Path) -> ClaimPacket:
    return ClaimPacket.model_validate_json(path.read_text(encoding="utf-8"))


def findings_for(packet: ClaimPacket):
    return classify_node(ClaimState(packet=packet))["findings"]


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
@pytest.mark.parametrize("profile", list(PROFILES))
def test_three_buckets_sum_to_gross(path: Path, profile: str) -> None:
    packet = load(path)
    result = compute_waterfall(packet, findings_for(packet), profile)

    assert (
        result.projected_settlement + result.patient_liability + result.hospital_writeoff
        == result.gross_bill
    )


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
@pytest.mark.parametrize("profile", list(PROFILES))
def test_settlement_within_bounds(path: Path, profile: str) -> None:
    packet = load(path)
    result = compute_waterfall(packet, findings_for(packet), profile)

    assert result.projected_settlement >= 0
    assert result.projected_settlement <= result.gross_bill


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
def test_hospital_writeoff_is_only_lists_two_three_four(path: Path) -> None:
    """List I is the patient's problem; Lists II/III/IV are the hospital's.

    Getting this backwards is the single most common error in naive versions of
    this tool, and it inverts the headline finding.
    """
    packet = load(path)
    findings = findings_for(packet)
    result = compute_waterfall(packet, findings, "typical")

    expected = sum(
        (
            f.deducted_amount
            for f in findings
            if f.classification in {"LIST_II_ROOM", "LIST_III_PROCEDURE", "LIST_IV_TREATMENT"}
        ),
        Decimal("0"),
    )
    assert result.hospital_writeoff == expected


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
def test_unmapped_items_are_never_silently_deducted(path: Path) -> None:
    packet = load(path)
    for f in findings_for(packet):
        if f.classification == "UNMAPPED":
            assert f.deducted_amount == 0


def test_room_downgrade_improves_settlement() -> None:
    packet = load(Path(__file__).parent.parent / "data" / "samples" / "cardiac.json")
    findings = findings_for(packet)

    outcome = simulate_room_downgrade(packet, findings, "typical")
    assert outcome is not None, "cardiac sample is built to breach the room cap"

    downgraded, gain = outcome
    assert gain > 0
    assert downgraded.projected_settlement > compute_waterfall(packet, findings, "typical").projected_settlement


def test_room_downgrade_only_reduces_the_stay_row() -> None:
    """A mixed stay must not drag a separately-billed ward row into the downgrade.

    The cap is 6000/day and the deluxe room bills 8000/day, so the per-day saving is
    2000. Only the deluxe-room row (unit_rate == room_stay.rate_per_day) may lose that
    saving; the ward row is a different room and must keep its full 4000 x 2.
    """
    packet = ClaimPacket(
        claim_id="SYNTHETIC-MIXED-ROOM-001",
        context=ClaimContext(
            claim_type="cashless",
            admission_date=date(2026, 6, 14),
            discharge_date=date(2026, 6, 19),
            primary_diagnosis="Severe pneumonia",
        ),
        policy=PolicyTerms(
            policy_id="SYNTH-POL-MIXED",
            sum_insured=Decimal("300000"),
            balance_sum_insured=Decimal("300000"),
            room_rent_cap_per_day=Decimal("6000"),
            icu_cap_per_day=Decimal("12000"),
        ),
        room_stay=RoomStay(
            room_category="Deluxe Room",
            rate_per_day=Decimal("8000"),
            days=5,
            is_icu=False,
        ),
        line_items=[
            BillLineItem(
                line_no=1,
                description="Room charges - Deluxe",
                head="ROOM",
                quantity=Decimal("5"),
                unit_rate=Decimal("8000"),
                amount=Decimal("40000"),
            ),
            BillLineItem(
                line_no=2,
                description="Room charges - Ward",
                head="ROOM",
                quantity=Decimal("2"),
                unit_rate=Decimal("4000"),
                amount=Decimal("8000"),
            ),
        ],
    )
    outcome = simulate_room_downgrade(packet, findings_for(packet), "typical")
    assert outcome is not None, "the deluxe room breaches the 6000/day cap"

    # The returned result carries the downgraded bill's gross, which is the exact
    # artifact the over-reduction corrupts. The stay row loses only its own saving
    # (8000 - 6000) x 5 = 10000, so the downgraded bill is 40000 - 10000 + 8000.
    downgraded, gain = outcome
    assert downgraded.gross_bill == Decimal("38000"), (
        "only the stay row may be reduced: the ward row billed separately at 4000 x 2 "
        "must come through untouched"
    )
    assert (
        downgraded.projected_settlement
        + downgraded.patient_liability
        + downgraded.hospital_writeoff
        == downgraded.gross_bill
    )


def test_room_downgrade_reduces_row_without_printed_rate() -> None:
    """Bills that print only the row total must still be reduced.

    extract.py's `_row_to_item` leaves `unit_rate` as Decimal("0") when the rate
    column is empty, and `room_stay_from_items` derives `rate_per_day = amount /
    quantity`. The stay row then carries `unit_rate == 0` while
    `room_stay.rate_per_day` is 8000, so matching on the printed unit rate finds no
    row and the downgrade reduces nothing -- a regression against the pre-fix loop
    that reduced every ROOM row.
    """
    packet = ClaimPacket(
        claim_id="SYNTHETIC-DERIVED-ROOM-001",
        context=ClaimContext(
            claim_type="cashless",
            admission_date=date(2026, 6, 14),
            discharge_date=date(2026, 6, 20),
            primary_diagnosis="Severe pneumonia",
        ),
        policy=PolicyTerms(
            policy_id="SYNTH-POL-DERIVED",
            sum_insured=Decimal("300000"),
            balance_sum_insured=Decimal("300000"),
            room_rent_cap_per_day=Decimal("6000"),
            icu_cap_per_day=Decimal("12000"),
        ),
        room_stay=RoomStay(
            room_category="General Ward",
            rate_per_day=Decimal("8000"),
            days=6,
            is_icu=False,
        ),
        line_items=[
            BillLineItem(
                line_no=1,
                description="Room charges - General",
                head="ROOM",
                quantity=Decimal("6"),
                unit_rate=Decimal("0"),
                amount=Decimal("48000"),
            ),
            BillLineItem(
                line_no=2,
                description="Room charges - Ward",
                head="ROOM",
                quantity=Decimal("2"),
                unit_rate=Decimal("4000"),
                amount=Decimal("8000"),
            ),
        ],
    )
    outcome = simulate_room_downgrade(packet, findings_for(packet), "typical")
    assert outcome is not None, "the 8000/day stay (48000/6) breaches the 6000/day cap"

    downgraded, gain = outcome
    assert downgraded.gross_bill == Decimal("44000"), (
        "the stay row must lose (8000 - 6000) x 6 = 12000 even though its printed "
        "unit_rate is 0: 48000 - 12000, plus the untouched 8000 ward row"
    )
    assert (
        downgraded.projected_settlement
        + downgraded.patient_liability
        + downgraded.hospital_writeoff
        == downgraded.gross_bill
    )


def test_room_downgrade_matches_repeating_derived_rate() -> None:
    """A stay whose derived rate is a repeating decimal must still match exactly.

    When the bill prints only the row total, `rate_per_day = amount / quantity` can
    be a repeating decimal (40000 / 3). Matching the stay row by a printed unit rate
    can never hit it; matching by the same derivation is exact because both sides are
    the same Decimal division.
    """
    packet = ClaimPacket(
        claim_id="SYNTHETIC-REPEATING-ROOM-001",
        context=ClaimContext(
            claim_type="cashless",
            admission_date=date(2026, 6, 14),
            discharge_date=date(2026, 6, 17),
            primary_diagnosis="Severe pneumonia",
        ),
        policy=PolicyTerms(
            policy_id="SYNTH-POL-REPEATING",
            sum_insured=Decimal("300000"),
            balance_sum_insured=Decimal("300000"),
            room_rent_cap_per_day=Decimal("6000"),
            icu_cap_per_day=Decimal("12000"),
        ),
        room_stay=RoomStay(
            room_category="Deluxe Room",
            rate_per_day=Decimal("40000") / Decimal("3"),
            days=3,
            is_icu=False,
        ),
        line_items=[
            BillLineItem(
                line_no=1,
                description="Room charges - Deluxe",
                head="ROOM",
                quantity=Decimal("3"),
                unit_rate=Decimal("0"),
                amount=Decimal("40000"),
            ),
        ],
    )
    outcome = simulate_room_downgrade(packet, findings_for(packet), "typical")
    assert outcome is not None, "the 40000/3 per-day stay breaches the 6000/day cap"

    downgraded, gain = outcome
    assert downgraded.gross_bill == Decimal("18000.00"), (
        "40000/3 per day against a 6000 cap saves 7333.33.../day for 3 days; "
        "40000 - 21999.99... = 18000.00 once quantized to the paisa"
    )
    assert (
        downgraded.projected_settlement
        + downgraded.patient_liability
        + downgraded.hospital_writeoff
        == downgraded.gross_bill
    )


def test_profile_ordering_is_monotonic() -> None:
    """Lenient must never settle below conservative, or the profiles are mislabelled."""
    packet = load(Path(__file__).parent.parent / "data" / "samples" / "cardiac.json")
    findings = findings_for(packet)

    results = {p: compute_waterfall(packet, findings, p) for p in PROFILES}
    assert (
        results["conservative"].projected_settlement
        <= results["typical"].projected_settlement
        <= results["lenient"].projected_settlement
    )


def test_audit_runs_without_a_key() -> None:
    """The whole app must work with AI switched off. No key, no crash, real numbers."""
    packet = load(Path(__file__).parent.parent / "data" / "samples" / "cardiac.json")
    result = audit(packet)

    assert result.gross_bill == Decimal("410000.00")
    assert result.narrative
    assert result.action_list
    assert set(result.profiles) == set(PROFILES)
