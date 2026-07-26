"""The invariant that catches real waterfall bugs.

Every rupee on the bill ends up in exactly one of three buckets. If they do not sum
to the gross bill, a deduction has been double-counted or attributed to the wrong
party -- the most common way this kind of engine goes quietly wrong.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from claimiq.graph import audit
from claimiq.nodes.classify import classify_node
from claimiq.state import ClaimPacket, ClaimState
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
