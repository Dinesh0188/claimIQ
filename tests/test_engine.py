"""Engine edge cases, and the distinction between 'clean' and 'could not check'.

The claim these tests defend: a result that looks confident is confident. Before the
verdict and coverage existed, a bill the reader could barely parse produced the same
green screen as a bill that passed every check.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from claimiq.graph import MIN_COVERAGE, audit
from claimiq.nodes.classify import classify_alias, classify_deterministic
from claimiq.state import BillLineItem, ClaimPacket

SAMPLES = Path(__file__).parent.parent / "data" / "samples"


def load(name: str) -> ClaimPacket:
    return ClaimPacket.model_validate_json((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def item(description: str, amount: str, head: str = "OTHER", **kw) -> BillLineItem:
    return BillLineItem(
        line_no=kw.pop("line_no", 1),
        description=description,
        head=head,
        amount=Decimal(amount),
        **kw,
    )


# --- the three verdicts are distinguishable -------------------------------


def test_a_clean_claim_says_so() -> None:
    packet = load("clean")
    los = (packet.context.discharge_date - packet.context.admission_date).days
    packet.line_items = [
        item("Room rent - General ward", str(5000 * los), "ROOM", quantity=Decimal(los))
    ]
    packet.room_stay.rate_per_day = Decimal("5000")
    packet.room_stay.days = los
    packet.context.documents_attached = [
        "DOC-CLAIM-FORM", "DOC-DISCHARGE-SUMMARY", "DOC-FINAL-BILL",
        "DOC-INDOOR-PAPERS", "DOC-INVESTIGATION-REPORTS", "DOC-PHARMACY-BILLS",
        "DOC-ID-PROOF",
    ]
    result = audit(packet, persist=False)

    assert result.verdict == "CLEAN"
    assert result.coverage == Decimal("1")
    assert not result.all_findings


def test_an_unreadable_bill_cannot_be_verified() -> None:
    """The case that used to render as two green success boxes."""
    packet = load("clean")
    packet.line_items = [
        item("ZZQX 4471", "80000", line_no=1),
        item("Room rent", "20000", "ROOM", line_no=2, quantity=Decimal("4")),
    ]
    result = audit(packet, persist=False)

    assert result.verdict == "CANNOT_VERIFY"
    assert result.coverage < MIN_COVERAGE
    assert any("could be assessed" in c for c in result.caveats)
    assert any("upper bound" in c for c in result.caveats), (
        "the user must be told which direction the error runs in"
    )


def test_a_claim_with_findings_needs_attention() -> None:
    result = audit(load("cardiac"), persist=False)

    assert result.verdict == "NEEDS_ATTENTION"
    assert result.all_findings


def test_cannot_verify_outranks_needs_attention() -> None:
    """A partial check that found two problems has not established there are only two."""
    packet = load("cardiac")
    packet.line_items.append(item("QQQ 9", "500000", line_no=99))
    result = audit(packet, persist=False)

    assert result.all_findings, "still has findings"
    assert result.verdict == "CANNOT_VERIFY", "but coverage must win"


# --- unassessed lines are never silently payable --------------------------


def test_an_unidentifiable_line_is_unmapped_not_payable() -> None:
    determination = classify_deterministic(item("MISC CHRG 4471", "90000"))

    assert determination.classification == "UNMAPPED"
    assert "manual review" in determination.reason.lower()


def test_unmapped_deducts_nothing() -> None:
    """UNMAPPED means 'we do not know', which may not cost the hospital money."""
    packet = load("clean")
    packet.line_items = [
        item("ZZQX 4471", "50000", line_no=1),
        item("Room rent", "20000", "ROOM", line_no=2, quantity=Decimal("4")),
    ]
    result = audit(packet, persist=False)

    unmapped = [f for f in result.findings if f.classification == "UNMAPPED"]
    assert unmapped
    assert all(f.deducted_amount == Decimal("0") for f in unmapped)


def test_coverage_counts_unassessed_money_not_unassessed_lines() -> None:
    """One unreadable ₹90,000 line matters more than nine unreadable ₹10 lines."""
    packet = load("clean")
    packet.line_items = [
        item("ZZQX 4471", "90000", line_no=1),
        item("Room rent", "10000", "ROOM", line_no=2, quantity=Decimal("2")),
    ]
    result = audit(packet, persist=False)

    assert result.coverage == Decimal("0.1")


# --- a primary service is never disallowed on a word collision ------------


@pytest.mark.parametrize(
    ("description", "head"),
    [
        ("CAP AMOXYCILLIN 500MG", "PHARMACY"),   # 'cap'
        ("Capnography monitoring", "INVESTIGATION"),
        ("Shoulder blade X-ray", "INVESTIGATION"),  # 'blade'
        ("Soap solution enema", "PHARMACY"),        # 'soap'
        ("CAPD dialysis", "PROCEDURE"),
        ("Surgeon fee - CABG", "PROCEDURE"),
        ("Room charges - Single AC", "ROOM"),
    ],
)
def test_a_primary_service_is_payable_whatever_words_it_contains(
    description: str, head: str
) -> None:
    """Regression. Alias matching on short words ('cap', 'blade', 'soap') disallowed
    real drugs and diagnostics until the billing head gated it."""
    determination = classify_alias(item(description, "1000", head))

    assert determination.classification == "PAYABLE", determination.reason


def test_a_genuine_non_payable_item_is_still_caught() -> None:
    """The guard above must not have switched the classifier off."""
    for description in ("Patient gown", "Bed pan", "Attendant food", "Registration charges"):
        determination = classify_alias(item(description, "500"))
        assert determination.classification != "PAYABLE", description
        assert determination.cited_chunk_id, f"{description} must cite a rule"


def test_capsule_is_not_a_cap() -> None:
    """Word-boundary matching, on a head=OTHER line where the head cannot save us."""
    assert classify_alias(item("Capsule omeprazole", "120")).classification == "PAYABLE"


def test_retrieval_never_deducts_a_primary_service() -> None:
    from claimiq.nodes.classify import classify_retrieval
    from claimiq.state import BillLineItem
    item = BillLineItem(
        line_no=1,
        description="Diapers",
        head="PROCEDURE",
        quantity="1",
        unit_rate="5000",
        amount="5000",
    )
    d = classify_retrieval(item)
    assert d.classification == "PAYABLE"


# --- money edge cases through the whole engine ----------------------------


def test_zero_value_lines_do_not_break_the_invariant() -> None:
    packet = load("clean")
    packet.line_items = [
        item("Room rent", "20000", "ROOM", line_no=1, quantity=Decimal("4")),
        item("Waived charge", "0", line_no=2),
    ]
    result = audit(packet, persist=False)
    typical = result.typical

    assert (
        typical.projected_settlement + typical.patient_liability + typical.hospital_writeoff
        == typical.gross_bill
    )


def test_a_negative_adjustment_survives_the_waterfall() -> None:
    """Credit notes are billed as negative lines and must not be absolutised."""
    packet = load("clean")
    packet.line_items = [
        item("Room rent", "20000", "ROOM", line_no=1, quantity=Decimal("4")),
        item("Refund - overcharge", "-2000", line_no=2),
    ]
    result = audit(packet, persist=False)

    assert result.gross_bill == Decimal("18000.00")
    typical = result.typical
    assert (
        typical.projected_settlement + typical.patient_liability + typical.hospital_writeoff
        == typical.gross_bill
    )


def test_duplicate_line_items_are_flagged_with_their_cost() -> None:
    packet = load("clean")
    packet.line_items = [
        item("Room rent", "20000", "ROOM", line_no=1, quantity=Decimal("4")),
        item("Dressing charges", "1500", line_no=2, service_date=date(2026, 3, 2)),
        item("Dressing charges", "1500", line_no=3, service_date=date(2026, 3, 2)),
    ]
    result = audit(packet, persist=False)

    flags = [f for f in result.consistency_flags if f.check_id == "CHK-DUPLICATE-LINE"]
    assert flags, "a repeated charge must be flagged"
    assert flags[0].impact == Decimal("1500"), "impact is the duplicate, not the pair"
    assert flags[0].severity == "WARNING"


def test_service_dates_outside_the_stay_are_flagged() -> None:
    packet = load("clean")
    admission = packet.context.admission_date
    packet.line_items = [
        item("Room rent", "20000", "ROOM", line_no=1, quantity=Decimal("4")),
        item("Pharmacy - post discharge", "800", "PHARMACY", line_no=2,
             service_date=packet.context.discharge_date.replace(
                 day=min(28, packet.context.discharge_date.day + 1))),
    ]
    result = audit(packet, persist=False)

    flags = [f for f in result.consistency_flags if f.check_id == "CHK-DATE-WINDOW"]
    assert flags
    assert str(admission) in flags[0].expected


def test_room_days_beyond_the_stay_are_a_blocker_with_a_rupee_impact() -> None:
    packet = load("clean")
    packet.room_stay.days = (
        packet.context.discharge_date - packet.context.admission_date
    ).days + 3
    result = audit(packet, persist=False)

    flags = [f for f in result.consistency_flags if f.check_id == "CHK-LOS-ROOM"]
    assert flags
    assert flags[0].severity == "BLOCKER"
    assert flags[0].impact == packet.room_stay.rate_per_day * 3


# --- every finding is attributable ----------------------------------------


def test_every_deduction_names_the_rule_that_caused_it() -> None:
    result = audit(load("cardiac"), persist=False)

    for finding in result.findings:
        if finding.classification.startswith("LIST_"):
            assert finding.rule_id, f"line {finding.line_no} deducts without a rule id"
            assert finding.citation, f"line {finding.line_no} deducts without a citation"
            assert finding.impact == finding.deducted_amount


def test_findings_are_sorted_most_severe_first() -> None:
    from claimiq.state import SEVERITY_ORDER

    findings = audit(load("cardiac"), persist=False).all_findings
    ranks = [SEVERITY_ORDER[f.severity] for f in findings]

    assert ranks == sorted(ranks)


def test_payable_lines_are_not_findings() -> None:
    """A finding is something to act on. 'This charge is fine' is not."""
    result = audit(load("cardiac"), persist=False)

    assert all(
        getattr(f, "classification", None) != "PAYABLE" for f in result.all_findings
    )


# --- the arithmetic is shown, not just asserted ---------------------------


def test_every_policy_deduction_shows_its_working() -> None:
    result = audit(load("cardiac"), persist=False)
    deductions = result.typical.policy_deductions

    assert deductions, "cardiac sample breaches its room cap"
    for deduction in deductions:
        assert deduction.inputs, f"{deduction.step} shows no inputs"
        assert deduction.formula, f"{deduction.step} shows no formula"


def test_the_pdf_builds_for_a_claim_with_gaps_and_flags() -> None:
    """Regression, and the reason nothing caught it: no test ever built a PDF.

    The severity colours were rendered with `.hexval()[2:]`, producing a bare
    `b3261e` with no `#`, which reportlab rejects. Any claim carrying a document gap
    or a consistency flag -- almost every claim -- crashed the report button.
    """
    from claimiq.report import build_report

    result = audit(load("cardiac"), persist=False)
    assert result.document_gaps and result.consistency_flags, "sample must exercise both"

    pdf = build_report(result, "typical")

    assert pdf.startswith(b"%PDF"), "must be a real PDF"
    assert len(pdf) > 2000


def test_the_pdf_builds_for_a_clean_claim() -> None:
    """The other branch: no gaps, no flags, nothing to colour."""
    from claimiq.report import build_report

    packet = load("clean")
    packet.context.documents_attached = [
        "DOC-CLAIM-FORM", "DOC-DISCHARGE-SUMMARY", "DOC-FINAL-BILL",
        "DOC-INDOOR-PAPERS", "DOC-INVESTIGATION-REPORTS", "DOC-PHARMACY-BILLS",
        "DOC-ID-PROOF",
    ]
    assert build_report(audit(packet, persist=False), "typical").startswith(b"%PDF")


def test_the_copay_basis_names_the_admissible_amount() -> None:
    """It used to say '10% co-pay on the admissible amount' without ever saying what
    the admissible amount was, which made the figure impossible to check."""
    result = audit(load("cardiac"), persist=False)
    copay = next(d for d in result.typical.policy_deductions if d.step == "copay")

    assert "admissible amount at this step" in copay.inputs
    assert "x" in copay.formula
