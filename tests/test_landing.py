"""The landing page may not become a mockup.

web/index.html shows figures, a rule id and a citation presented as real output from
the cardiac sample. Marketing pages drift: the engine changes, nobody re-runs the
screenshot, and what was true becomes a claim. These tests re-run that audit and fail
if the page and the engine have parted company.

They are deliberately strict about the numbers and deliberately silent about wording.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from claimiq.graph import audit
from claimiq.money import rupees
from claimiq.state import ClaimPacket

ROOT = Path(__file__).parent.parent
PAGE = ROOT / "web" / "index.html"
SAMPLE = ROOT / "data" / "samples" / "cardiac.json"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def visible(page: str) -> str:
    """The page with HTML comments stripped.

    The comments explain the rules this page is held to, and naming a banned phrase in
    order to forbid it must not trip the check that forbids it.
    """
    return re.sub(r"<!--.*?-->", "", page, flags=re.DOTALL)


@pytest.fixture(scope="module")
def result():
    packet = ClaimPacket.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    return audit(packet, persist=False)


# --- the figures on the page are the engine's figures ----------------------


def test_the_headline_figures_are_real(page: str, result) -> None:
    typical = result.typical
    for label, value in (
        ("gross bill", result.gross_bill),
        ("settlement", typical.projected_settlement),
        ("patient liability", typical.patient_liability),
        ("hospital write-off", typical.hospital_writeoff),
    ):
        assert rupees(value) in page, (
            f"landing page no longer shows the real {label} ({rupees(value)}). "
            f"Re-check web/index.html against a live audit before shipping it."
        )


def test_the_quoted_finding_is_real(page: str, result) -> None:
    """The page opens one finding: surgical drapes, L3-001, Rs 1,800."""
    finding = next(
        (f for f in result.findings if f.cited_chunk_id == "L3-001"), None
    )
    assert finding is not None, "L3-001 no longer fires on the cardiac sample"
    assert "L3-001" in page
    assert rupees(finding.deducted_amount) in page
    assert finding.citation in page, (
        f"the citation on the page has drifted from the corpus: {finding.citation}"
    )


def test_the_quoted_arithmetic_is_real(page: str, result) -> None:
    """The page reproduces the proportionate-deduction working."""
    deduction = next(
        d for d in result.typical.policy_deductions if d.step == "room_rent_proportionate"
    )
    assert deduction.formula in page, (
        f"the arithmetic block is stale. Engine now produces: {deduction.formula}"
    )
    assert rupees(deduction.amount) in page


def test_the_severity_counts_are_real(page: str, result) -> None:
    from collections import Counter

    counts = Counter(f.severity for f in result.all_findings)
    claimed = re.search(r"(\d+) blocker, (\d+) warning, (\d+) info", page)
    assert claimed, "the verdict line on the page no longer states severity counts"

    assert [int(g) for g in claimed.groups()] == [
        counts["BLOCKER"], counts["WARNING"], counts["INFO"]
    ]


def test_the_verdict_matches(page: str, result) -> None:
    assert result.verdict == "NEEDS_ATTENTION"
    assert "Needs attention" in page


# --- measured claims are the measured ones ---------------------------------


def test_performance_claims_match_the_benchmark_report(page: str) -> None:
    """The two percentages on the page must equal what bench_classify.py last wrote.

    A marketing page quoting a recall figure nobody re-measured is exactly the kind of
    invented statistic this project refuses to publish.
    """
    report = (ROOT / "CLASSIFICATION.md").read_text(encoding="utf-8")
    row = re.search(
        r"\|\s*deterministic\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%", report
    )
    assert row, "CLASSIFICATION.md no longer has a deterministic row to check against"

    _, recall, false_deduction = row.groups()
    assert f"{recall}%" in page, f"page quotes a recall figure other than {recall}%"
    assert f"{false_deduction}%" in page, (
        f"page quotes a false-deduction rate other than {false_deduction}%"
    )


# --- nothing fabricated ----------------------------------------------------


def test_no_invented_social_proof(visible: str) -> None:
    """No logos, testimonials or customer counts. There are no customers yet."""
    banned = [
        "testimonial", "trusted by", "customers say", "our clients",
        "case study", "5-star", "award-winning",
    ]
    lowered = visible.lower()
    for phrase in banned:
        assert phrase not in lowered, f"landing page contains unearned social proof: {phrase!r}"


def test_unearned_numbers_are_placeholders_not_guesses(page: str) -> None:
    """Deployment stats we do not have must be marked TODO, never estimated."""
    assert "PLACEHOLDER" in page, (
        "the placeholder for real deployment figures has been removed — if it was "
        "replaced with actual data that is fine, but check it was not replaced with a "
        "plausible-looking estimate"
    )


def test_the_limitations_are_stated_not_buried(page: str) -> None:
    """Procurement asks these. The page must answer them on the page."""
    for claim in (
        "no authentication",
        "unverified snapshot",
        "synthetic",
        "Estimates, not adjudications",
    ):
        assert claim.lower() in page.lower(), f"landing page no longer discloses: {claim}"


# --- basic page health -----------------------------------------------------


def test_required_meta_tags_are_present(page: str) -> None:
    for tag in (
        'name="viewport"',
        'name="description"',
        'property="og:title"',
        'property="og:description"',
        'property="og:image"',
        'name="twitter:card"',
        '<html lang="en">',
    ):
        assert tag in page, f"missing {tag}"


def test_referenced_assets_exist(page: str) -> None:
    for asset in re.findall(r'(?:href|src|content)="(/[^"]+)"', page):
        if asset.startswith("/docs"):
            continue  # served by FastAPI, not a file
        assert (ROOT / "web" / asset.lstrip("/")).is_file(), f"missing asset {asset}"


def test_every_image_has_alt_text(page: str) -> None:
    for tag in re.findall(r"<img\b[^>]*>", page):
        assert "alt=" in tag, f"image without alt text: {tag}"
