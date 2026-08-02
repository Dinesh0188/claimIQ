"""The money-making formula: turning a detected leak into a recovery case.

Everything else in this system answers *what will this claim settle at*. This module
answers the question a hospital's finance director actually asks, which is different:
**how much money does fixing this get me back, per year, and what do I fix first.**

The distinction that makes the number honest
--------------------------------------------
Not every deduction is recoverable, and a tool that adds them all up is selling a
number the hospital will never see.

  recoverable      Lists II/III/IV. The hospital billed separately for something
                   already subsumed in the room, procedure or treatment charge. It is
                   a charge-master defect: fix the master and the money stops leaving.
                   The hospital keeps it by BILLING CORRECTLY, not by appealing.

  not recoverable  Room-rent cap and the proportionate deduction. Co-pay. Deductible.
                   Sub-limits. These are policy terms the patient agreed to. They are
                   large -- usually far larger than the leak -- and counting them would
                   roughly quintuple the headline. They are somebody else's money.

  not recoverable  List I. Optional items the patient pays for. Already billed to the
                   right party; there is nothing to recover.

So `recoverable` here means exactly one thing: **hospital-borne item deductions**. That
is `bearer == 'HOSPITAL'`, and it is the only bucket where a billing change converts
directly into retained revenue.

The formula
-----------
Per repeating item, over an observed window of `n` claims::

    incidence          = claims_affected / n
    avg_per_occurrence = total_written_off / claims_affected
    leak_per_claim     = total_written_off / n          (incidence x avg)
    annual_recovery    = leak_per_claim x annual_claim_volume

`annual_claim_volume` is the one input that is not observed. It is taken from the
operator when they know it, and otherwise extrapolated from the run rate actually seen
-- claims per distinct month, times twelve. That extrapolation is stated on the screen
rather than hidden, because it is the assumption the whole projection rests on and a
hospital running 300 claims a month will get a very different answer from one running
300 a year.

What this is not
----------------
It is a projection of what *stops leaking* if the charge master is corrected, under a
stated volume assumption, computed from claims this system actually audited. It is not
a guarantee, it is not a promise about any specific insurer's behaviour, and it does
not include the cost of doing the remediation. Those caveats travel with the numbers
(see `assumptions`) rather than living in a footnote nobody reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from claimiq.state import BEARER_OF
from claimiq.store import claims_df, findings_df

# Classifications whose deduction the hospital absorbs and can stop absorbing. Derived
# from BEARER_OF rather than retyped, so a new list added to the vocabulary cannot be
# silently left out of the recovery model -- which would understate it forever without
# anything failing.
RECOVERABLE_CLASSES = {c for c, bearer in BEARER_OF.items() if bearer == "HOSPITAL"}

MONTHS_PER_YEAR = 12


def _sum(column) -> Decimal:
    total = column.sum() if len(column) else 0
    return total if isinstance(total, Decimal) else Decimal(str(total))


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


@dataclass
class ItemRecovery:
    """One repeating billing-master defect, priced."""

    item: str
    claims_affected: int
    total_written_off: Decimal
    incidence: Decimal
    avg_per_occurrence: Decimal
    leak_per_claim: Decimal
    annual_recovery: Decimal
    cited_rule: str = ""

    def as_dict(self) -> dict:
        return {
            "item": self.item,
            "claims_affected": self.claims_affected,
            "total_written_off": _money(self.total_written_off),
            # A percentage, because "appears on 30% of claims" is the sentence a
            # billing manager repeats to their team. The rupee figure is what gets it
            # prioritised; the incidence is what makes it believable.
            "incidence_pct": str((self.incidence * 100).quantize(Decimal("0.1"))),
            "avg_per_occurrence": _money(self.avg_per_occurrence),
            "leak_per_claim": _money(self.leak_per_claim),
            "annual_recovery": _money(self.annual_recovery),
            "cited_rule": self.cited_rule,
        }


@dataclass
class RecoveryModel:
    claims_audited: int
    months_observed: int
    gross_billed: Decimal
    recoverable_total: Decimal
    leak_per_claim: Decimal
    leak_rate_pct: Decimal
    annual_claim_volume: int
    volume_source: str
    annual_recovery: Decimal
    items: list[ItemRecovery] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "empty": False,
            "claims_audited": self.claims_audited,
            "months_observed": self.months_observed,
            "gross_billed": _money(self.gross_billed),
            "recoverable_total": _money(self.recoverable_total),
            "leak_per_claim": _money(self.leak_per_claim),
            "leak_rate_pct": str(self.leak_rate_pct.quantize(Decimal("0.01"))),
            "annual_claim_volume": self.annual_claim_volume,
            "volume_source": self.volume_source,
            "annual_recovery": _money(self.annual_recovery),
            # The top three carry most of the money and all of the momentum: a
            # remediation list of forty items does not get done, and three does.
            "top_three_recovery": _money(
                sum((i.annual_recovery for i in self.items[:3]), Decimal("0"))
            ),
            "items": [i.as_dict() for i in self.items],
            "assumptions": self.assumptions,
        }


def recovery_model(
    tenant: str | None = None,
    annual_claim_volume: int | None = None,
    limit: int = 12,
) -> dict:
    """Price the recoverable leak and rank what to fix first."""
    claims = claims_df(tenant)
    findings = findings_df(tenant)

    if claims.empty:
        return {
            "empty": True,
            "reason": "No audited claims yet. Audit a few, or seed the demo portfolio "
            "with `python scripts/seed_db.py`.",
        }

    n = len(claims)
    gross = _sum(claims.gross_bill)

    leaking = findings[
        findings.classification.isin(RECOVERABLE_CLASSES) & (findings.deducted > 0)
    ].copy()
    recoverable_total = _sum(leaking.deducted)

    leak_per_claim = recoverable_total / n if n else Decimal("0")
    leak_rate_pct = (recoverable_total / gross * 100) if gross else Decimal("0")

    months = int(claims.month.nunique()) or 1
    volume, source = _annual_volume(annual_claim_volume, n, months)
    annual_recovery = leak_per_claim * volume

    return RecoveryModel(
        claims_audited=n,
        months_observed=months,
        gross_billed=gross,
        recoverable_total=recoverable_total,
        leak_per_claim=leak_per_claim,
        leak_rate_pct=leak_rate_pct,
        annual_claim_volume=volume,
        volume_source=source,
        annual_recovery=annual_recovery,
        items=_rank_items(leaking, n, volume, limit),
        assumptions=_assumptions(n, months, volume, source),
    ).as_dict()


def _annual_volume(given: int | None, claims: int, months: int) -> tuple[int, str]:
    """The volume the projection multiplies by, and where it came from.

    Returned as a pair on purpose. A projection whose denominator is invisible invites
    the reader to assume it was measured, and this one usually is not -- so the source
    travels with the number to every screen that shows it.
    """
    if given and given > 0:
        return int(given), "supplied by you"
    run_rate = round(claims / months * MONTHS_PER_YEAR)
    return int(run_rate), (
        f"extrapolated from {claims} claims over {months} month(s) "
        f"({round(claims / months)}/month x 12)"
    )


def _rank_items(leaking, claims_audited: int, volume: int, limit: int) -> list[ItemRecovery]:
    if leaking.empty:
        return []

    # Normalised, because the same defect arrives spelled six ways -- "STRL GLV 7.5",
    # "Sterile gloves (OT)", "strl glv 7.5". Grouping on the raw string scatters one
    # charge-master line across several rows and buries it below items that leak less.
    leaking["key"] = leaking.description.str.lower().str.strip()

    grouped = (
        leaking.groupby("key")
        .agg(
            claims_affected=("claim_id", "nunique"),
            total=("deducted", "sum"),
            rule=("cited_chunk_id", "first"),
        )
        .sort_values("total", ascending=False)
        .head(limit)
        .reset_index()
    )

    items = []
    for row in grouped.itertuples():
        affected = int(row.claims_affected)
        total = row.total if isinstance(row.total, Decimal) else Decimal(str(row.total))
        per_claim = total / claims_audited
        items.append(
            ItemRecovery(
                item=row.key,
                claims_affected=affected,
                total_written_off=total,
                incidence=Decimal(affected) / claims_audited,
                avg_per_occurrence=total / affected if affected else Decimal("0"),
                leak_per_claim=per_claim,
                annual_recovery=per_claim * volume,
                cited_rule=row.rule or "",
            )
        )
    return items


def _assumptions(claims: int, months: int, volume: int, source: str) -> list[str]:
    return [
        "Recoverable means hospital-borne item deductions only — Lists II, III and IV. "
        "Room-rent, co-pay, deductible and sub-limit deductions are policy terms borne "
        "by the patient and are deliberately excluded.",
        f"Measured over {claims} audited claim(s) across {months} month(s).",
        f"Projected against {volume:,} claims/year — {source}.",
        "Recovery requires correcting the charge master. This is the leak that stops, "
        "not money that arrives on its own.",
    ]
