"""Internal-consistency checks -- the things a TPA queries a packet for.

Each check is a plain function returning a flag or None, registered in CHECKS.
Deterministic on purpose: these are arithmetic and date comparisons, and a model
adds nothing but the chance of being wrong.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from claimiq.state import SEVERITY_ORDER, ClaimPacket, ConsistencyFlag, ItemFinding

Check = tuple[str, str]


def _los_days(packet: ClaimPacket) -> int:
    return (packet.context.discharge_date - packet.context.admission_date).days


def check_room_days_vs_los(packet: ClaimPacket, findings: list[ItemFinding]) -> ConsistencyFlag | None:
    los = _los_days(packet)
    if packet.room_stay.days > los:
        return ConsistencyFlag(
            check_id="CHK-LOS-ROOM",
            rule_id="CHK-LOS-ROOM",
            severity="BLOCKER",
            field="room_stay.days",
            observed=f"{packet.room_stay.days} days billed",
            expected=f"at most {los} days (admission to discharge)",
            impact=packet.room_stay.rate_per_day * (packet.room_stay.days - los),
            message=(
                f"Room billed for {packet.room_stay.days} days but admission to discharge "
                f"spans {los} days. The excess will be disallowed and is a common cause of "
                "outright rejection."
            ),
        )
    return None


def check_room_total(packet: ClaimPacket, findings: list[ItemFinding]) -> ConsistencyFlag | None:
    billed = sum(
        (li.amount for li in packet.line_items if li.head == "ROOM"), Decimal("0")
    )
    expected = packet.room_stay.rate_per_day * packet.room_stay.days
    if billed and abs(billed - expected) > Decimal("1"):
        return ConsistencyFlag(
            check_id="CHK-ROOM-TOTAL",
            rule_id="CHK-ROOM-TOTAL",
            severity="WARNING",
            field="line_items[head=ROOM].amount",
            observed=f"Rs {billed:,} billed",
            expected=f"Rs {expected:,} "
            f"({packet.room_stay.rate_per_day:,} x {packet.room_stay.days} days)",
            impact=abs(billed - expected),
            message=(
                f"Room line items total Rs {billed:,} but the declared tariff implies "
                f"Rs {expected:,} ({packet.room_stay.rate_per_day:,} x {packet.room_stay.days}). "
                "Reconcile before submission."
            ),
        )
    return None


def check_service_dates(packet: ClaimPacket, findings: list[ItemFinding]) -> ConsistencyFlag | None:
    outside = [
        li.line_no
        for li in packet.line_items
        if li.service_date
        and not (packet.context.admission_date <= li.service_date <= packet.context.discharge_date)
    ]
    if outside:
        return ConsistencyFlag(
            check_id="CHK-DATE-WINDOW",
            rule_id="CHK-DATE-WINDOW",
            severity="WARNING",
            field="line_items.service_date",
            observed=f"{len(outside)} line(s) dated outside the stay",
            expected=f"{packet.context.admission_date} to {packet.context.discharge_date}",
            impact=sum(
                (li.amount for li in packet.line_items if li.line_no in set(outside)),
                Decimal("0"),
            ),
            message=(
                f"{len(outside)} line item(s) are dated outside the admission window "
                f"(lines {', '.join(map(str, outside[:8]))}). Pharmacy dated after discharge "
                "is one of the most frequently queried findings."
            ),
        )
    return None


def check_preauth_variance(packet: ClaimPacket, findings: list[ItemFinding]) -> ConsistencyFlag | None:
    approved = packet.context.preauth_approved_amount
    if not approved or approved <= 0:
        return None
    gross = packet.gross_bill
    if gross > approved * Decimal("1.1"):
        variance = (gross - approved) / approved * 100
        return ConsistencyFlag(
            check_id="CHK-PREAUTH-VARIANCE",
            rule_id="CHK-PREAUTH-VARIANCE",
            severity="BLOCKER",
            field="gross_bill vs context.preauth_approved_amount",
            observed=f"Rs {gross:,} billed",
            expected=f"Rs {approved:,} pre-authorised (10% tolerance)",
            impact=gross - approved,
            message=(
                f"Final bill Rs {gross:,} exceeds the pre-authorised Rs {approved:,} by "
                f"{variance:.0f}%. File an enhancement request before submitting, or expect "
                "the excess to be held."
            ),
        )
    return None


def check_duplicates(packet: ClaimPacket, findings: list[ItemFinding]) -> ConsistencyFlag | None:
    seen = Counter(
        (li.description.strip().lower(), li.amount, li.service_date) for li in packet.line_items
    )
    dupes = [key for key, count in seen.items() if count > 1]
    if dupes:
        names = ", ".join(sorted({d[0] for d in dupes})[:5])
        return ConsistencyFlag(
            check_id="CHK-DUPLICATE-LINE",
            rule_id="CHK-DUPLICATE-LINE",
            severity="WARNING",
            field="line_items",
            observed=f"{len(dupes)} description/amount/date combination(s) billed twice",
            expected="each charge billed once",
            impact=sum(
                (amount * (count - 1) for (_, amount, _), count in seen.items() if count > 1),
                Decimal("0"),
            ),
            message=f"{len(dupes)} line item(s) appear more than once with identical amount and date: {names}.",
        )
    return None


def check_icu_declared_but_not_billed(
    packet: ClaimPacket, findings: list[ItemFinding]
) -> ConsistencyFlag | None:
    if not packet.room_stay.is_icu:
        return None
    if not any("icu" in li.description.lower() for li in packet.line_items):
        return ConsistencyFlag(
            check_id="CHK-ICU-NO-CHARGE",
            rule_id="CHK-ICU-NO-CHARGE",
            severity="INFO",
            field="room_stay.is_icu",
            observed="declared ICU, no ICU line on the bill",
            expected="an ICU charge, or the stay recorded as non-ICU",
            message="Stay is declared as ICU but no ICU line item appears on the bill.",
        )
    return None


def check_unmapped_share(packet: ClaimPacket, findings: list[ItemFinding]) -> ConsistencyFlag | None:
    unmapped = sum(
        (f.amount for f in findings if f.classification == "UNMAPPED"), Decimal("0")
    )
    gross = packet.gross_bill
    if gross > 0 and unmapped / gross > Decimal("0.10"):
        return ConsistencyFlag(
            check_id="CHK-UNMAPPED-SHARE",
            rule_id="CHK-UNMAPPED-SHARE",
            # Raised from INFO. This is the check that says "we could not assess a
            # tenth of this bill" -- the one finding that undermines every number on
            # the screen. Filing it alongside "your amounts look rounded" was wrong.
            severity="WARNING",
            field="line_items.classification",
            observed=f"Rs {unmapped:,} unassessed ({unmapped / gross:.0%} of gross)",
            expected="every line matched to a rule or confirmed payable",
            impact=unmapped,
            message=(
                f"Rs {unmapped:,} ({unmapped / gross:.0%} of the bill) could not be matched "
                "to a catalog entry. The estimate below is correspondingly less reliable."
            ),
        )
    return None


def check_round_numbers(packet: ClaimPacket, findings: list[ItemFinding]) -> ConsistencyFlag | None:
    """Weak signal only. Flagged as advisory, never as an accusation."""
    items = [li for li in packet.line_items if li.amount >= 1000]
    if len(items) < 8:
        return None
    round_ones = [li for li in items if li.amount % Decimal("1000") == 0]
    share = len(round_ones) / len(items)
    if share > 0.7:
        return ConsistencyFlag(
            check_id="CHK-ROUND-NUMBER",
            rule_id="CHK-ROUND-NUMBER",
            severity="INFO",
            field="line_items.amount",
            observed=f"{len(round_ones)} of {len(items)} items are exact multiples of 1,000",
            expected="itemised amounts",
            message=(
                f"{share:.0%} of line items above Rs 1,000 are exact multiples of 1,000. "
                "Not evidence of anything on its own, but bills that look estimated rather "
                "than itemised attract scrutiny."
            ),
        )
    return None


def check_daycare_length(packet: ClaimPacket, findings: list[ItemFinding]) -> ConsistencyFlag | None:
    los = _los_days(packet)
    if los == 0 and packet.room_stay.days > 0:
        return ConsistencyFlag(
            check_id="CHK-DAYCARE-LOS",
            rule_id="CHK-DAYCARE-LOS",
            severity="INFO",
            field="context.admission_date / discharge_date",
            observed="same-day admission and discharge, with room charges",
            expected="an overnight stay, or a day-care filing",
            message=(
                "Admission and discharge fall on the same day but room charges are billed. "
                "Confirm whether this should be filed as a day-care procedure."
            ),
        )
    return None


CHECKS = [
    check_room_days_vs_los,
    check_room_total,
    check_service_dates,
    check_preauth_variance,
    check_duplicates,
    check_icu_declared_but_not_billed,
    check_unmapped_share,
    check_round_numbers,
    check_daycare_length,
]


def run_checks(packet: ClaimPacket, findings: list[ItemFinding]) -> list[ConsistencyFlag]:
    flags = [flag for check in CHECKS if (flag := check(packet, findings)) is not None]
    return sorted(flags, key=lambda f: SEVERITY_ORDER[f.severity])
