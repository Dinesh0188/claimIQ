"""Money arithmetic, including the edge cases that hide rounding bugs.

The engine claims Decimal end to end and half-up rounding. These tests are what makes
that claim checkable, because the three places that previously implemented it
separately disagreed and nothing caught them.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from claimiq.money import (
    ROUNDING_NOTE,
    as_decimal,
    from_paise,
    quantize,
    rupees,
    to_paise,
)

# --- rounding rule --------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.005", "0.01"),   # half goes UP, not to even
        ("0.015", "0.02"),   # banker's rounding would give 0.02 here too
        ("0.025", "0.03"),   # ...but would give 0.02 here. This is the discriminator.
        ("0.045", "0.05"),   # and 0.04 under banker's
        ("-0.025", "-0.03"),  # half-up on negatives means away from zero
        ("1.004", "1.00"),
        ("1.006", "1.01"),
    ],
)
def test_quantize_is_half_up_not_bankers(value: str, expected: str) -> None:
    assert quantize(Decimal(value)) == Decimal(expected)


def test_display_rounding_matches_engine_rounding() -> None:
    """The bug this file exists for.

    `rupees()` used to be int(round(float(value))) -- banker's rounding on a float --
    while the engine used ROUND_HALF_UP on a Decimal. A .50 amount was displayed one
    rupee lower than the engine had computed, on exactly half the values.
    """
    assert rupees(Decimal("1234.50")) == "₹1,235"
    assert rupees(Decimal("1235.50")) == "₹1,236"  # banker's would give 1,236 too
    assert rupees(Decimal("1236.50")) == "₹1,237"  # banker's would give 1,236
    assert round(1236.5) == 1236, "sanity: Python's round really is banker's"


def test_rounding_note_is_stated() -> None:
    """A rounding rule the user cannot read is not a disclosed rounding rule."""
    assert "paisa" in ROUNDING_NOTE
    assert "halves up" in ROUNDING_NOTE or "half" in ROUNDING_NOTE


# --- no float anywhere ----------------------------------------------------


def test_no_float_survives_the_conversion_chain() -> None:
    for value in ("0.1", "0.2", "0.3"):
        assert isinstance(as_decimal(value), Decimal)
        assert isinstance(quantize(value), Decimal)
        assert isinstance(from_paise(to_paise(value)), Decimal)


def test_the_classic_float_failure_does_not_occur() -> None:
    """0.1 + 0.2 != 0.3 in binary floating point. It must here."""
    assert quantize("0.1") + quantize("0.2") == quantize("0.3")


def test_a_float_input_is_routed_through_str_not_binary() -> None:
    """pandas hands back numpy floats; accepting them must not import the error."""
    assert as_decimal(0.1) == Decimal("0.1")
    assert as_decimal(2.675) == Decimal("2.675")  # Decimal(2.675) would be 2.67499...


# --- paise round trip -----------------------------------------------------


@pytest.mark.parametrize(
    "value", ["0", "0.01", "1", "1234.56", "410000.00", "99999999.99", "-500.25"]
)
def test_paise_round_trip_is_exact(value: str) -> None:
    assert from_paise(to_paise(Decimal(value))) == Decimal(value).quantize(Decimal("0.01"))


def test_paise_is_an_integer_never_a_float() -> None:
    result = to_paise(Decimal("410000.00"))
    assert isinstance(result, int)
    assert result == 41_000_000


def test_none_is_zero_not_a_crash() -> None:
    assert to_paise(None) == 0
    assert from_paise(None) == Decimal("0.00")


# --- edge cases -----------------------------------------------------------


def test_zero_amounts() -> None:
    assert quantize(Decimal("0")) == Decimal("0.00")
    assert rupees(Decimal("0")) == "₹0"
    assert to_paise(Decimal("0")) == 0


def test_negative_adjustments_keep_their_sign() -> None:
    """Credit notes and refunds are negative. They must not be silently absolutised."""
    assert quantize(Decimal("-1500.005")) == Decimal("-1500.01")
    assert rupees(Decimal("-1500")) == "-₹1,500"
    assert to_paise(Decimal("-1500")) == -150_000


def test_missing_value_renders_as_a_dash_not_zero() -> None:
    """None means 'not calculable'. Showing it as ₹0 asserts something false."""
    assert rupees(None) == "—"


# --- Indian grouping ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", "₹0"),
        ("999", "₹999"),
        ("1000", "₹1,000"),
        ("99999", "₹99,999"),
        ("100000", "₹1,00,000"),        # one lakh: grouping changes to 2s here
        ("410000", "₹4,10,000"),
        ("1000000", "₹10,00,000"),
        ("10000000", "₹1,00,00,000"),   # one crore
        ("123456789", "₹12,34,56,789"),
    ],
)
def test_lakh_and_crore_grouping(value: str, expected: str) -> None:
    assert rupees(Decimal(value)) == expected


def test_paise_are_shown_when_asked_for() -> None:
    """The PDF reconciles against the hospital's own system, so it needs the paisa."""
    assert rupees(Decimal("410000.50"), paise=True) == "₹4,10,000.50"
    assert rupees(Decimal("0.05"), paise=True) == "₹0.05"
