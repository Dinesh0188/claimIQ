"""The deduction waterfall. Deterministic, Decimal, no LLM anywhere near it.

This is the tool the agent calls. Arithmetic is the one part of this system that can
be verified exactly, so it is kept exact -- which is what lets the verifier node catch
the model when its narrative disagrees with these numbers.

Invariant, enforced by construction and asserted in tests:

    projected_settlement + patient_liability + hospital_writeoff == gross_bill
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from claimiq.state import (
    ClaimPacket,
    Head,
    ItemFinding,
    PolicyDeduction,
    WaterfallResult,
)

PAISE = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(PAISE, rounding=ROUND_HALF_UP)


# Which billing heads are NOT dragged into the proportionate deduction.
#
# This varies by insurer and it is the least-understood rule in Indian health
# insurance, so the tool ships three stances and reports a range rather than
# pretending to know which one a given insurer will apply.
PROFILES: dict[str, dict] = {
    "conservative": {
        "label": "Conservative (insurer-friendly)",
        "note": "Only pharmacy and implants escape the proportionate deduction.",
        "excluded_heads": ["PHARMACY", "IMPLANT"],
    },
    "typical": {
        "label": "Typical",
        "note": "Pharmacy, implants and diagnostics escape; surgeon/OT/nursing do not.",
        "excluded_heads": ["PHARMACY", "IMPLANT", "INVESTIGATION"],
    },
    "lenient": {
        "label": "Lenient (insured-friendly)",
        "note": "Consultations also escape the proportionate deduction.",
        "excluded_heads": ["PHARMACY", "IMPLANT", "INVESTIGATION", "CONSULTATION"],
    },
}


def _payable_by_head(findings: list[ItemFinding]) -> dict[Head, Decimal]:
    """Amount still on the claim per head, after item-level deductions."""
    totals: dict[Head, Decimal] = {}
    for f in findings:
        payable = f.amount - f.deducted_amount
        totals[f.head] = totals.get(f.head, Decimal("0")) + payable
    return totals


def compute_waterfall(
    packet: ClaimPacket,
    findings: list[ItemFinding],
    profile: str = "typical",
) -> WaterfallResult:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; expected one of {list(PROFILES)}")

    gross = _money(packet.gross_bill)
    policy = packet.policy
    room = packet.room_stay

    # --- step 1: item-level deductions (Lists I-IV) ---------------------
    patient_items = _money(
        sum((f.deducted_amount for f in findings if f.bearer == "PATIENT"), Decimal("0"))
    )
    hospital_items = _money(
        sum((f.deducted_amount for f in findings if f.bearer == "HOSPITAL"), Decimal("0"))
    )
    item_deduction_total = patient_items + hospital_items

    running = gross - item_deduction_total
    deductions: list[PolicyDeduction] = []

    def apply(step: str, basis: str, amount: Decimal) -> None:
        """Record a deduction, never letting the claim go negative."""
        nonlocal running
        taken = _money(min(max(amount, Decimal("0")), running))
        if taken > 0:
            deductions.append(PolicyDeduction(step=step, basis=basis, amount=taken))
            running -= taken

    by_head = _payable_by_head(findings)

    # --- step 2: room rent cap + proportionate deduction ----------------
    cap = policy.icu_cap_per_day if room.is_icu else policy.room_rent_cap_per_day
    if cap and cap > 0 and room.rate_per_day > cap:
        room_payable = by_head.get("ROOM", Decimal("0"))
        excess = min((room.rate_per_day - cap) * room.days, room_payable)
        apply(
            "room_rent_excess",
            f"Room billed at Rs {room.rate_per_day:,}/day against a cap of Rs {cap:,}/day "
            f"for {room.days} days.",
            excess,
        )

        # Associated charges get scaled by the same ratio the room exceeded its cap.
        ratio = cap / room.rate_per_day
        excluded = set(PROFILES[profile]["excluded_heads"]) | {"ROOM"}
        associated = sum(
            (amt for head, amt in by_head.items() if head not in excluded), Decimal("0")
        )
        apply(
            "room_rent_proportionate",
            f"Associated charges of Rs {_money(associated):,} scaled by "
            f"(1 - {cap:,}/{room.rate_per_day:,}) under the '{profile}' profile.",
            associated * (Decimal("1") - ratio),
        )

    # --- step 3: procedure sub-limit ------------------------------------
    procedure = (packet.context.procedure_performed or "").lower()
    for name, limit in policy.procedure_sublimits.items():
        if name.lower() in procedure and running > limit:
            apply(
                "procedure_sublimit",
                f"Policy caps '{name}' at Rs {limit:,}.",
                running - limit,
            )
            break

    # --- step 4: deductible ---------------------------------------------
    if policy.deductible > 0:
        apply("deductible", f"Policy deductible of Rs {policy.deductible:,}.", policy.deductible)

    # --- step 5: co-pay --------------------------------------------------
    if policy.copay_percent > 0:
        apply(
            "copay",
            f"{policy.copay_percent}% co-pay on the admissible amount.",
            running * policy.copay_percent / Decimal("100"),
        )

    # --- step 6: balance sum insured ------------------------------------
    if running > policy.balance_sum_insured:
        apply(
            "sum_insured_cap",
            f"Balance sum insured is Rs {policy.balance_sum_insured:,}.",
            running - policy.balance_sum_insured,
        )

    policy_total = sum((d.amount for d in deductions), Decimal("0"))

    return WaterfallResult(
        profile=profile,
        gross_bill=gross,
        item_deduction_total=item_deduction_total,
        policy_deductions=deductions,
        projected_settlement=running,
        # The patient absorbs their own optional items plus every policy-level
        # deduction. Freshers routinely misattribute the proportionate deduction to
        # the hospital -- it lands here.
        patient_liability=patient_items + policy_total,
        hospital_writeoff=hospital_items,
    )


def simulate_room_downgrade(
    packet: ClaimPacket,
    findings: list[ItemFinding],
    profile: str = "typical",
) -> tuple[WaterfallResult, Decimal] | None:
    """Recompute as if the patient had taken a room inside the cap.

    Returns (result, gain) or None when the room was already within cap.
    """
    cap = packet.policy.icu_cap_per_day if packet.room_stay.is_icu else packet.policy.room_rent_cap_per_day
    if not cap or packet.room_stay.rate_per_day <= cap:
        return None

    baseline = compute_waterfall(packet, findings, profile)

    downgraded = packet.model_copy(deep=True)
    downgraded.room_stay.rate_per_day = cap
    saved_per_day = packet.room_stay.rate_per_day - cap
    for item in downgraded.line_items:
        if item.head == "ROOM":
            item.amount = _money(item.amount - saved_per_day * item.quantity)
            item.unit_rate = cap

    # Findings carry amounts too, so they have to move with the bill.
    adjusted = [f.model_copy(deep=True) for f in findings]
    by_line = {li.line_no: li.amount for li in downgraded.line_items}
    for f in adjusted:
        if f.head == "ROOM":
            f.amount = by_line.get(f.line_no, f.amount)

    result = compute_waterfall(downgraded, adjusted, profile)
    return result, result.projected_settlement - baseline.projected_settlement
