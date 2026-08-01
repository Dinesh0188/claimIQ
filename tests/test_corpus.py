"""The rule source contract: every rule is attributable, and the loader fails closed.

These tests are the enforcement mechanism for the claim the product makes on screen.
If they pass, no rule can reach a user without naming the document it came from.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from claimiq.retrieval.corpus import (
    REQUIRED_KEYS,
    Chunk,
    CorpusError,
    RuleSource,
    _parse_file,
    corpus_version,
    load_corpus,
    sources,
    stale_sources,
)
from claimiq.state import SEVERITY_ORDER

VALID_LISTS = {"LIST_I_OPTIONAL", "LIST_II_ROOM", "LIST_III_PROCEDURE", "LIST_IV_TREATMENT"}


# --- every rule is attributable -------------------------------------------


def test_every_chunk_carries_every_required_attribute() -> None:
    for chunk in load_corpus():
        for key in REQUIRED_KEYS:
            assert getattr(chunk, key if key != "list" else "list_name"), (
                f"{chunk.chunk_id} is missing {key}"
            )


def test_every_source_id_resolves_to_a_registered_document() -> None:
    registry = sources()
    for chunk in load_corpus():
        assert chunk.source_id in registry, f"{chunk.chunk_id} cites unknown source"


def test_every_severity_is_a_known_level() -> None:
    for chunk in load_corpus():
        assert chunk.severity in SEVERITY_ORDER, chunk.chunk_id


def test_citation_precision_never_overclaims() -> None:
    """`item` means somebody checked this line against the official annexure.

    Nothing has been checked yet, so nothing may claim item-level precision. When a
    human does verify one, this test is what they update -- deliberately, not by
    accident.
    """
    overclaiming = [c.chunk_id for c in load_corpus() if c.citation_precision == "item"]
    assert not overclaiming, (
        f"{overclaiming} claim item-level citations. Item precision requires a human to "
        f"have matched the text against the source document."
    )


def test_attribution_names_the_reference_number() -> None:
    chunk = next(c for c in load_corpus() if c.source_id == "IRDA-2016-146")
    assert "IRDA/HLT/REG/CIR/146/07/2016" in chunk.attribution
    assert "Annexure" in chunk.attribution


# --- classification metadata still holds ----------------------------------


def test_bearer_matches_the_list_it_belongs_to() -> None:
    """List I is the patient's cost; II/III/IV are the hospital's. This mapping is the
    product's whole argument, so it is asserted rather than assumed."""
    for chunk in load_corpus():
        if chunk.list_name == "LIST_I_OPTIONAL":
            assert chunk.bearer == "PATIENT", chunk.chunk_id
        elif chunk.list_name in VALID_LISTS:
            assert chunk.bearer == "HOSPITAL", chunk.chunk_id


def test_severity_follows_the_bearer() -> None:
    """The derivation the user signed off: the patient's own cost is INFO, a hospital
    billing error is a WARNING because it repeats on every claim."""
    for chunk in load_corpus():
        if chunk.bearer == "PATIENT":
            assert chunk.severity == "INFO", chunk.chunk_id
        elif chunk.bearer == "HOSPITAL":
            assert chunk.severity == "WARNING", chunk.chunk_id


# --- the loader fails closed ----------------------------------------------


def test_a_chunk_missing_a_citation_refuses_to_load(tmp_path) -> None:
    path = tmp_path / "bad.md"
    path.write_text(
        "<!-- source:\njurisdiction: IN\nlist: LIST_II_ROOM\nbearer: HOSPITAL\n"
        "severity: WARNING\nsource_id: IRDA-2016-146\ncitation_precision: list\n-->\n\n"
        "<!-- chunk_id: X-001 -->\n### Something\nBody text.\n",
        encoding="utf-8",
    )
    with pytest.raises(CorpusError, match="citation"):
        _parse_file(path)


def test_a_file_with_no_source_block_refuses_to_load(tmp_path) -> None:
    path = tmp_path / "orphan.md"
    path.write_text("<!-- chunk_id: X-001 -->\n### Something\nBody.\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="source"):
        _parse_file(path)


def test_an_unknown_severity_refuses_to_load(tmp_path) -> None:
    path = tmp_path / "bad_severity.md"
    path.write_text(
        "<!-- source:\njurisdiction: IN\nlist: LIST_II_ROOM\nbearer: HOSPITAL\n"
        "severity: CATASTROPHIC\nsource_id: IRDA-2016-146\ncitation: Annexure II\n"
        "citation_precision: list\n-->\n\n"
        "<!-- chunk_id: X-001 -->\n### Something\nBody.\n",
        encoding="utf-8",
    )
    with pytest.raises(CorpusError):
        _parse_file(path)


def test_a_chunk_may_override_a_file_default(tmp_path) -> None:
    """The upgrade path for a rule somebody has actually verified."""
    path = tmp_path / "override.md"
    path.write_text(
        "<!-- source:\njurisdiction: IN\nlist: LIST_II_ROOM\nbearer: HOSPITAL\n"
        "severity: WARNING\nsource_id: IRDA-2016-146\ncitation: Annexure II\n"
        "citation_precision: list\n-->\n\n"
        "<!-- chunk_id: X-001 -->\n### Default\nBody.\n\n"
        "<!-- chunk_id: X-002 | severity: BLOCKER | citation: Annexure II item 4 -->\n"
        "### Overridden\nBody.\n",
        encoding="utf-8",
    )
    default, overridden = _parse_file(path)
    assert default.severity == "WARNING"
    assert overridden.severity == "BLOCKER"
    assert overridden.citation == "Annexure II item 4"


# --- version stamp --------------------------------------------------------


def test_version_changes_when_metadata_changes(monkeypatch) -> None:
    """The bug this catches moved money without changing the stamp.

    `corpus_version()` hashed only chunk_id and search_text, so flipping a bearer from
    HOSPITAL to PATIENT -- which moves a deduction from the hospital's write-off to the
    patient's bill -- produced an identical version string. Two audits could carry the
    same stamp and disagree about who pays.
    """
    original = load_corpus()
    baseline = corpus_version()

    target = next(c.chunk_id for c in original if c.bearer == "HOSPITAL")
    flipped = [
        c.model_copy(update={"bearer": "PATIENT"}) if c.chunk_id == target else c
        for c in original
    ]
    monkeypatch.setattr("claimiq.retrieval.corpus.load_corpus", lambda: flipped)
    corpus_version.cache_clear()
    try:
        assert corpus_version() != baseline
    finally:
        corpus_version.cache_clear()


def test_version_is_marked_unverified_while_any_source_is() -> None:
    """The stamp may not claim more confidence than its weakest source."""
    assert corpus_version().startswith("unverified-") == bool(stale_sources())


# --- freshness ------------------------------------------------------------


def test_unverified_sources_are_reported_as_stale() -> None:
    """Every source in use is unverified today. That must be visible, not implicit."""
    stale = stale_sources()
    assert stale, "sources are all unverified, so all must be reported"
    for reason in stale.values():
        assert reason


def test_only_sources_actually_in_use_are_reported() -> None:
    """IRDAI-2024-MC is registered as a possible superseder but no chunk cites it.
    Warning about it would train people to dismiss the warning."""
    assert "IRDAI-2024-MC" in sources()
    assert "IRDAI-2024-MC" not in stale_sources()


def test_a_verified_source_goes_stale_after_a_year() -> None:
    source = RuleSource(
        source_id="X",
        jurisdiction="IN",
        title="T",
        published=date(2020, 1, 1),
        retrieved=date(2020, 1, 1),
        verified_on=date(2020, 1, 1),
        status="in_force",
    )
    assert source.staleness(date(2020, 6, 1)) is None
    assert "Last verified" in (source.staleness(date(2020, 1, 1) + timedelta(days=400)) or "")


def test_a_superseded_source_names_its_replacement() -> None:
    source = RuleSource(
        source_id="X",
        jurisdiction="IN",
        title="T",
        published=date(2016, 1, 1),
        retrieved=date(2026, 1, 1),
        verified_on=date(2026, 1, 1),
        status="superseded",
        superseded_by="IRDAI-2024-MC",
    )
    assert "IRDAI-2024-MC" in (source.staleness(date(2026, 1, 2)) or "")


# --- integrity ------------------------------------------------------------


def test_chunk_ids_are_unique() -> None:
    ids = [c.chunk_id for c in load_corpus()]
    assert len(ids) == len(set(ids))


def test_every_chunk_has_searchable_text() -> None:
    for chunk in load_corpus():
        assert isinstance(chunk, Chunk)
        assert len(chunk.search_text.strip()) > 20, chunk.chunk_id
