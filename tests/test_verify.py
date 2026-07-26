"""The verifier must catch a narrative that disagrees with the tool output.

This is the test that proves the guardrail works. It injects a deliberately
inconsistent explanation rather than hoping a real model produces one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claimiq.nodes.classify import classify_items
from claimiq.nodes.compute import compute_node
from claimiq.nodes.verify import needs_repair, verify_node
from claimiq.state import ClaimPacket, ClaimState

SAMPLE = Path(__file__).parent.parent / "data" / "samples" / "cardiac.json"


@pytest.fixture
def state() -> ClaimState:
    packet = ClaimPacket.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    findings = classify_items(packet.line_items, strategy="deterministic")
    st = ClaimState(packet=packet, findings=findings)
    st.profiles = compute_node(st)["profiles"]
    return st


def test_invented_figure_is_caught(state: ClaimState) -> None:
    state.narrative = (
        "The estimated settlement is Rs 3,71,428 after a proportionate deduction "
        "of Rs 88,215."
    )
    out = verify_node(state)

    assert out["verify_passed"] is False
    assert any("never produced" in p for p in out["verify_problems"])


def test_faithful_narrative_passes(state: ClaimState) -> None:
    typical = state.profiles["typical"]
    state.narrative = (
        f"Gross bill Rs {typical.gross_bill:,.2f}. Estimated settlement "
        f"Rs {typical.projected_settlement:,.2f}, with patient liability "
        f"Rs {typical.patient_liability:,.2f}."
    )
    out = verify_node(state)

    assert out["verify_passed"] is True, out["verify_problems"]


def test_small_integers_are_not_treated_as_money(state: ClaimState) -> None:
    """"8 days" and "10% co-pay" must not trip the reconciliation check."""
    typical = state.profiles["typical"]
    state.narrative = (
        f"Over 8 days with a 10% co-pay, the estimated settlement is "
        f"Rs {typical.projected_settlement:,.2f}."
    )
    out = verify_node(state)

    assert out["verify_passed"] is True, out["verify_problems"]


def test_broken_invariant_is_reported_as_an_engine_bug(state: ClaimState) -> None:
    state.narrative = "Nothing to see here."
    state.profiles["typical"].projected_settlement += 1  # corrupt the arithmetic
    out = verify_node(state)

    assert out["verify_passed"] is False
    assert any("ENGINE BUG" in p for p in out["verify_problems"])


def test_repair_loop_is_bounded(state: ClaimState) -> None:
    """A model that cannot be talked into consistency must not loop forever."""
    state.verify_passed = False
    state.repair_count = 99
    assert needs_repair(state) == "report"

    state.repair_count = 1
    assert needs_repair(state) == "explain"


def test_uncited_model_deduction_is_rejected(state: ClaimState) -> None:
    """Only model output needs a citation; the keyword table is auditable already."""
    for finding in state.findings:
        if finding.classification.startswith("LIST_"):
            finding.source = "llm"
            finding.cited_chunk_id = None
            break
    else:
        pytest.skip("sample produced no non-payable findings")

    state.narrative = ""
    out = verify_node(state)

    assert out["verify_passed"] is False
    assert any("cite no catalog entry" in p for p in out["verify_problems"])


def test_numbers_quoted_from_our_own_flags_are_grounded(state: ClaimState) -> None:
    """Regression: the verifier used to reject its own advisory text.

    The round-number check says "exact multiples of 1,000". The explain node is handed
    that sentence as a fact, so quoting it back is grounded -- but the money regex saw
    a bare 1,000 and called it invented, burning all three repairs on the clean claim.
    """
    from claimiq.state import ConsistencyFlag

    state.consistency_flags = [
        ConsistencyFlag(
            check_id="CHK-ROUND-NUMBER",
            severity="ADVISORY",
            message="91% of line items above Rs 1,000 are exact multiples of 1,000.",
        )
    ]
    state.narrative = "Many line items appear to be exact multiples of 1,000."

    assert verify_node(state)["verify_passed"] is True


def test_preauth_variance_is_grounded(state: ClaimState) -> None:
    """Regression: the variance is supplied as a fact, so stating it is not invention.

    Previously the engine emitted gross and pre-auth but never their difference, so a
    narrative quoting the variance in rupees -- the number a billing desk actually
    needs -- was rejected as hallucinated.
    """
    approved = state.packet.context.preauth_approved_amount
    assert approved, "cardiac sample carries a pre-auth amount"
    variance = state.packet.gross_bill - approved

    state.narrative = f"The final bill exceeds the approved amount by Rs {variance:,.2f}."

    assert verify_node(state)["verify_passed"] is True, verify_node(state)["verify_problems"]


def test_keyword_deduction_does_not_need_a_citation(state: ClaimState) -> None:
    state.narrative = ""
    for finding in state.findings:
        finding.source = "keyword"
        finding.cited_chunk_id = None

    assert verify_node(state)["verify_passed"] is True
