"""Money. One implementation, one rounding rule, no floats.

There used to be three implementations of this and they disagreed:

  claimiq/tools/waterfall.py  quantize to paise, ROUND_HALF_UP     -- the engine
  claimiq/store.py            Decimal -> integer paise, half-up    -- persistence
  ui/_shared.py               int(round(float(value)))             -- display

The third is the interesting one. `float()` throws away the exactness the other two
were built to preserve, and Python's built-in `round()` is banker's rounding, so it
breaks ties toward even: round(1234.5) is 1234, not 1235. The display layer was
rounding by a different rule than the engine, on a value it had already made inexact.
Nobody would notice on one bill. On a portfolio it drifts, and "money is never float"
should be true everywhere or it should not be claimed.

WHERE ROUNDING HAPPENS
Amounts are exact to the paisa through the whole engine and are rounded exactly twice:
once to the paisa when a deduction is recorded (`quantize`), and once to the whole
rupee when a figure is shown to a human (`rupees`). Both are ROUND_HALF_UP. The stored
value is never rounded -- `to_paise` is exact by construction.

`ROUNDING_NOTE` is the sentence shown in the product. It exists because a user
comparing our figure with their own arithmetic deserves to know which way we broke a
half-paisa tie, and that answer should not require reading the source.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

PAISE = Decimal("0.01")
RUPEE = Decimal("1")
ROUNDING = ROUND_HALF_UP

ROUNDING_NOTE = (
    "Amounts are exact to the paisa. Figures on screen are rounded to the nearest "
    "rupee, halves up."
)


def as_decimal(value: Decimal | int | str | float) -> Decimal:
    """Coerce to Decimal without going through binary float.

    `float` is accepted and routed via `str` rather than rejected: pandas hands back
    numpy floats from the database, and refusing them here would only push the
    conversion somewhere less careful.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def quantize(value: Decimal | int | str | float) -> Decimal:
    """Round to the paisa, half-up. What the engine records."""
    return as_decimal(value).quantize(PAISE, rounding=ROUNDING)


def to_paise(value: Decimal | int | str | float | None) -> int:
    """Rupees to exact integer paise. What gets stored."""
    if value is None:
        return 0
    return int((as_decimal(value) * 100).quantize(RUPEE, rounding=ROUNDING))


def from_paise(paise: int | None) -> Decimal:
    """Integer paise back to exact rupees. What comes out of the database.

    The inverse of `to_paise`, and it must stay exact: the SQLite views used to do
    this with `/ 100.0`, which is REAL division, so every figure the dashboard and the
    text-to-SQL feature ever showed was a float.
    """
    return (Decimal(paise or 0) / 100).quantize(PAISE)


def rupees(value: Decimal | int | str | float | None, paise: bool = False) -> str:
    """Format for an Indian hospital billing desk: 1,23,456 not 123,456.

    Lakh/crore grouping, because that is how the number will be read aloud. Pass
    `paise=True` where the exact figure matters more than scanability -- the audit PDF
    uses it so a printed report reconciles to the paisa against the hospital's system.
    """
    if value is None:
        return "—"

    amount = as_decimal(value)
    quantum = PAISE if paise else RUPEE
    amount = amount.quantize(quantum, rounding=ROUNDING)

    sign = "-" if amount < 0 else ""
    amount = abs(amount)

    whole = int(amount)
    fraction = f"{amount - whole:.2f}"[1:] if paise else ""

    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        digits = ",".join(parts + [tail])

    return f"{sign}₹{digits}{fraction}"
