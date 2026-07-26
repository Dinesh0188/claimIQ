"""Internal-consistency checks -- the things a TPA queries a packet for.

Each check is a plain function returning a flag or None, registered in CHECKS.
Deterministic on purpose: these are arithmetic and date comparisons, and a model
adds nothing but the chance of being wrong.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from claimiq.state import ClaimPacket, ConsistencyFlag, ItemFinding

Check = tuple[str, str]


def _los_days(packet: ClaimPacket) -> int:
    return (packet.context.discharge_date - packet.context.admission_date).days


def check_room_days_vs_los(packet: ClaimPacket, findings: list[ItemFinding]) -> ConsistencyFlag | None:
    los = _los_days(packet)
    if packet.room_stay.days > los:
        return ConsistencyFlag(
            check_id="CHK-LOS-ROOM",
            severity="BLOCKER",
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
            severity="QUERY_LIKELY",
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
            severity="QUERY_LIKELY",
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
            severity="BLOCKER",
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
            severity="QUERY_LIKELY",
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
            severity="ADVISORY",
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
            severity="ADVISORY",
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
            severity="ADVISORY",
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
            severity="ADVISORY",
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
    order = {"BLOCKER": 0, "QUERY_LIKELY": 1, "ADVISORY": 2}
    flags = [flag for check in CHECKS if (flag := check(packet, findings)) is not None]
    return sorted(flags, key=lambda f: order[f.severity])
