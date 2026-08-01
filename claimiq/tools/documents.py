"""Conditional document checklist.

The original spec proposed YAML rules evaluated with a sandboxed expression
evaluator. That is the right answer when non-engineers edit the rules; here it
would be a parser and a security question in exchange for nothing, since the
conditions are a dozen Python predicates that read perfectly well as code.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import NamedTuple

from claimiq.state import SEVERITY_ORDER, ClaimPacket, DocumentGap, Severity


class Requirement(NamedTuple):
    document_id: str
    name: str
    severity: Severity
    reason: str
    applies: Callable[[ClaimPacket], bool]


REQUIREMENTS: list[Requirement] = [
    Requirement(
        "DOC-CLAIM-FORM",
        "Signed claim form (Parts A and B)",
        "BLOCKER",
        "Required on every claim regardless of type.",
        lambda p: True,
    ),
    Requirement(
        "DOC-DISCHARGE-SUMMARY",
        "Discharge summary",
        "BLOCKER",
        "Establishes diagnosis, line of treatment and length of stay.",
        lambda p: True,
    ),
    Requirement(
        "DOC-FINAL-BILL",
        "Itemised final bill",
        "BLOCKER",
        "A consolidated bill without item detail cannot be adjudicated line by line.",
        lambda p: True,
    ),
    Requirement(
        "DOC-MLC",
        "Medico-legal certificate or FIR copy",
        "BLOCKER",
        "Accident claims require an MLC or FIR to establish the cause of injury.",
        lambda p: p.context.is_accident,
    ),
    Requirement(
        "DOC-IMPLANT-INV",
        "Implant invoice with batch number and sticker",
        "BLOCKER",
        "Implant cost is routinely disallowed in full without the original invoice and batch sticker.",
        lambda p: p.context.involves_implant
        or any(li.head == "IMPLANT" for li in p.line_items),
    ),
    Requirement(
        "DOC-PED-HISTORY",
        "Past treatment records / first consultation papers",
        "BLOCKER",
        "Needed to establish the date of first diagnosis against the pre-existing disease waiting period.",
        lambda p: p.context.is_ped_related,
    ),
    Requirement(
        "DOC-INDOOR-PAPERS",
        "Indoor case papers",
        "WARNING",
        "High-value and reimbursement claims are routinely queried for indoor case papers.",
        lambda p: p.gross_bill > Decimal("100000") or p.context.claim_type == "reimbursement",
    ),
    Requirement(
        "DOC-INVESTIGATION-REPORTS",
        "Investigation reports matching billed diagnostics",
        "WARNING",
        "Investigations billed without corresponding reports are held pending query.",
        lambda p: any(li.head == "INVESTIGATION" for li in p.line_items),
    ),
    Requirement(
        "DOC-OT-NOTES",
        "Operation theatre notes",
        "WARNING",
        "Surgical claims require OT notes to substantiate the procedure billed.",
        lambda p: any(li.head == "PROCEDURE" for li in p.line_items),
    ),
    Requirement(
        "DOC-PHARMACY-BILLS",
        "Pharmacy bills with prescriptions",
        "WARNING",
        "Pharmacy charges are disallowed where no matching prescription is on file.",
        lambda p: any(li.head == "PHARMACY" for li in p.line_items),
    ),
    Requirement(
        "DOC-PREAUTH-ENHANCEMENT",
        "Pre-authorisation enhancement request",
        "BLOCKER",
        "The final bill materially exceeds the approved pre-authorisation; an enhancement "
        "request must be on record before submission.",
        lambda p: bool(
            p.context.preauth_approved_amount
            and p.gross_bill > p.context.preauth_approved_amount * Decimal("1.1")
        ),
    ),
    Requirement(
        "DOC-ID-PROOF",
        "Photo identity proof of the insured",
        "INFO",
        "Standard KYC attachment; its absence delays rather than blocks.",
        lambda p: True,
    ),
]


def check_documents(packet: ClaimPacket) -> list[DocumentGap]:
    attached = {d.strip().upper() for d in packet.context.documents_attached}

    gaps = [
        DocumentGap(
            document_id=req.document_id,
            rule_id=req.document_id,
            name=req.name,
            severity=req.severity,
            reason=req.reason,
            field="context.documents_attached",
            observed="not attached",
            expected=req.name,
        )
        for req in REQUIREMENTS
        if req.applies(packet) and req.document_id.upper() not in attached
    ]
    return sorted(gaps, key=lambda g: SEVERITY_ORDER[g.severity])
