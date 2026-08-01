"""The guardrail node.

Three things are checked, and only the third needs the model to have behaved:

1. Arithmetic invariant -- the three buckets must sum to the gross bill. This is a
   bug in our own engine if it fails, not the model's fault.
2. Citation coverage -- every non-payable determination must name a real catalog
   chunk. An uncited deduction cannot be defended to a hospital, so it is not shippable.
3. Narrative reconciliation -- every rupee figure appearing in the model's prose must
   correspond to a figure the deterministic tool actually produced.

Check 3 is the one that only exists because the arithmetic is deterministic. If the
numbers came from the model there would be nothing to reconcile against.
"""

from __future__ import annotations

import re
from decimal import Decimal

from claimiq.state import ClaimState

MAX_REPAIRS = 2
# Ignore small integers -- "3 documents", "8 days", "10% co-pay" are not money.
MONEY_FLOOR = Decimal("1000")

_NUMBER = re.compile(r"(?:Rs\.?\s*|₹\s*)?(\d[\d,]{3,}(?:\.\d{1,2})?)")


def _known_figures(state: ClaimState) -> set[Decimal]:
    """Every number the engine actually computed, in the forms it might be written."""
    figures: set[Decimal] = set()
    for waterfall in state.profiles.values():
        figures.update(
            {
                waterfall.gross_bill,
                waterfall.projected_settlement,
                waterfall.patient_liability,
                waterfall.hospital_writeoff,
                waterfall.item_deduction_total,
            }
        )
        figures.update(d.amount for d in waterfall.policy_deductions)
    figures.update(f.amount for f in state.findings)
    figures.update(f.deducted_amount for f in state.findings)
    figures.add(state.packet.room_stay.rate_per_day)
    figures.add(state.packet.policy.room_rent_cap_per_day or Decimal("0"))
    figures.add(state.packet.policy.sum_insured)
    figures.add(state.packet.policy.balance_sum_insured)

    preauth = state.packet.context.preauth_approved_amount
    if preauth:
        figures.add(preauth)
        # The variance is supplied to the writer as a fact, so quoting it is grounded.
        figures.add(abs(state.packet.gross_bill - preauth))

    # Numbers appearing in our own flag and gap text are supplied facts too -- the
    # explain node is handed these strings verbatim. Without this, quoting our own
    # "exact multiples of 1,000" advisory back to us counted as a hallucination.
    for text in (
        [f.message for f in state.consistency_flags]
        + [g.reason for g in state.document_gaps]
        + [d.basis for w in state.profiles.values() for d in w.policy_deductions]
    ):
        for raw in _NUMBER.findall(text):
            try:
                figures.add(Decimal(raw.replace(",", "")))
            except Exception:  # noqa: BLE001
                continue

    # Accept both the exact value and its rounded form, since prose rounds.
    expanded = set()
    for value in figures:
        expanded.add(value)
        expanded.add(value.quantize(Decimal("1")))
    return expanded


def _unsupported_figures(state: ClaimState) -> list[str]:
    known = _known_figures(state)
    bad = []
    for raw in _NUMBER.findall(state.narrative):
        try:
            value = Decimal(raw.replace(",", ""))
        except Exception:  # noqa: BLE001
            continue
        if value < MONEY_FLOOR:
            continue
        if value not in known and value.quantize(Decimal("1")) not in known:
            bad.append(raw)
    return bad


def verify_node(state: ClaimState) -> dict:
    problems: list[str] = []

    # 1. arithmetic
    for name, waterfall in state.profiles.items():
        total = (
            waterfall.projected_settlement
            + waterfall.patient_liability
            + waterfall.hospital_writeoff
        )
        if total != waterfall.gross_bill:
            problems.append(
                f"ENGINE BUG: '{name}' buckets sum to {total} but gross is "
                f"{waterfall.gross_bill}."
            )

    # 2. citations -- enforced on EVERY deduction, whichever strategy produced it.
    #
    #    This used to exempt the keyword path, on the argument that a hand-written
    #    table is auditable source code and has no chunk to point at. Both halves are
    #    now false: the matcher is built from corpus aliases, so every match has a
    #    chunk, and the exemption was doing real harm -- keyword ran first, so the one
    #    strategy that could not cite was the one that decided most deductions.
    uncited = [
        f.line_no
        for f in state.findings
        if f.classification.startswith("LIST_") and not f.cited_chunk_id
    ]
    if uncited:
        problems.append(
            f"{len(uncited)} deduction(s) cite no catalog entry (lines "
            f"{', '.join(map(str, uncited[:10]))}). Money may not be moved by a rule "
            f"that cannot be pointed at."
        )

    # 3. narrative reconciliation
    unsupported = _unsupported_figures(state)
    if unsupported:
        problems.append(
            "The explanation contains figures the engine never produced: "
            + ", ".join(unsupported[:6])
            + ". Every number must come from the supplied facts."
        )

    return {
        "verify_problems": problems,
        "verify_passed": not problems,
        "repair_count": state.repair_count + (1 if problems else 0),
    }


def needs_repair(state: ClaimState) -> str:
    """Conditional edge: loop back into explain, or move on."""
    if state.verify_passed or state.repair_count > MAX_REPAIRS:
        return "report"
    return "explain"
