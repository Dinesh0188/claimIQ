"""Corpus integrity and retrieval sanity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claimiq.retrieval.corpus import corpus_version, load_corpus
from claimiq.retrieval.search import get_index

QUERIES = Path(__file__).parent.parent / "data" / "eval" / "retrieval_queries.json"

VALID_LISTS = {
    "LIST_I_OPTIONAL", "LIST_II_ROOM", "LIST_III_PROCEDURE", "LIST_IV_TREATMENT",
}


def test_corpus_loads_and_ids_are_unique() -> None:
    chunks = load_corpus()
    assert len(chunks) > 80
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_non_payable_chunks_declare_list_and_bearer() -> None:
    for chunk in load_corpus():
        if chunk.chunk_id.startswith(("L1-", "L2-", "L3-", "L4-")):
            assert chunk.list_name in VALID_LISTS, chunk.chunk_id
            assert chunk.bearer in {"PATIENT", "HOSPITAL"}, chunk.chunk_id
            assert chunk.aliases, f"{chunk.chunk_id} has no aliases"


def test_bearer_matches_list() -> None:
    """List I is the patient's cost; II/III/IV are the hospital's. Never mixed up."""
    for chunk in load_corpus():
        if chunk.list_name == "LIST_I_OPTIONAL":
            assert chunk.bearer == "PATIENT", chunk.chunk_id
        elif chunk.list_name in VALID_LISTS:
            assert chunk.bearer == "HOSPITAL", chunk.chunk_id


def test_corpus_version_is_stable_and_marked_unverified() -> None:
    assert corpus_version() == corpus_version()
    assert corpus_version().startswith("unverified-")


def test_every_benchmark_query_targets_a_real_chunk() -> None:
    ids = {c.chunk_id for c in load_corpus()}
    data = json.loads(QUERIES.read_text(encoding="utf-8"))
    for case in data["queries"]:
        missing = set(case["expected"]) - ids
        assert not missing, f"{case['query']!r} expects unknown chunk(s) {missing}"


@pytest.mark.parametrize(
    "query,expected",
    [
        ("STRL GLV 7.5", "L3-002"),
        ("PATIENT GOWN DISPOSABLE", "L2-001"),
        ("ATTENDER FOOD CHRG", "L1-009"),
        ("REGN CHARGES", "L4-001"),
    ],
)
def test_representative_queries_retrieve_the_right_chunk(query: str, expected: str) -> None:
    hits = get_index().search(query, k=5, strategy="rrf")
    assert expected in {h.chunk.chunk_id for h in hits}
