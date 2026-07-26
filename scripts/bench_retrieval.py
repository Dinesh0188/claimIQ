"""Measure retrieval quality. Answers "did hybrid actually help?" with a number.

    python scripts/bench_retrieval.py

Writes RETRIEVAL.md so the README can cite measured figures instead of asserting
that fusion works.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claimiq.retrieval.corpus import corpus_version  # noqa: E402
from claimiq.retrieval.search import get_index  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
QUERIES = ROOT / "data" / "eval" / "retrieval_queries.json"
STRATEGIES = ("bm25", "dense", "rrf")


def evaluate(index, queries: list[dict], strategy: str) -> dict:
    hits_at_1 = hits_at_3 = hits_at_5 = 0
    misses: list[str] = []

    for case in queries:
        results = index.search(case["query"], k=5, strategy=strategy)
        ids = [h.chunk.chunk_id for h in results]
        expected = set(case["expected"])

        if ids[:1] and expected & set(ids[:1]):
            hits_at_1 += 1
        if expected & set(ids[:3]):
            hits_at_3 += 1
        if expected & set(ids[:5]):
            hits_at_5 += 1
        else:
            misses.append(f"{case['query']!r} -> expected {sorted(expected)}, got {ids[:3]}")

    total = len(queries)
    return {
        "recall@1": hits_at_1 / total,
        "recall@3": hits_at_3 / total,
        "recall@5": hits_at_5 / total,
        "misses": misses,
    }


def main() -> None:
    data = json.loads(QUERIES.read_text(encoding="utf-8"))
    queries = data["queries"]
    index = get_index()

    if not index.dense_available:
        print("WARNING: dense retrieval unavailable; dense/rrf rows will be meaningless.\n")

    results = {s: evaluate(index, queries, s) for s in STRATEGIES}

    print(f"corpus {corpus_version()} | {len(index.chunks)} chunks | {len(queries)} labelled queries\n")
    header = f"{'strategy':<10} {'recall@1':>9} {'recall@3':>9} {'recall@5':>9}"
    print(header)
    print("-" * len(header))
    for strategy in STRATEGIES:
        r = results[strategy]
        print(
            f"{strategy:<10} {r['recall@1']:>8.1%} {r['recall@3']:>9.1%} {r['recall@5']:>9.1%}"
        )

    best_single = max(results["bm25"]["recall@5"], results["dense"]["recall@5"])
    delta = results["rrf"]["recall@5"] - best_single
    print(f"\nRRF vs best single strategy @5: {delta:+.1%}")
    if delta < 0:
        print("RRF is NOT helping here. Keep the simpler strategy.")

    lines = [
        "# Retrieval benchmark",
        "",
        f"Corpus `{corpus_version()}` — {len(index.chunks)} chunks, "
        f"{len(queries)} hand-labelled queries.",
        "",
        "Queries are written to imitate real hospital bill line printing and are "
        "deliberately not copied from the corpus alias lists, so this measures "
        "generalisation rather than lookup.",
        "",
        "| strategy | recall@1 | recall@3 | recall@5 |",
        "|---|---|---|---|",
    ]
    for strategy in STRATEGIES:
        r = results[strategy]
        lines.append(
            f"| {strategy} | {r['recall@1']:.1%} | {r['recall@3']:.1%} | {r['recall@5']:.1%} |"
        )
    lines += ["", f"RRF vs best single strategy at k=5: **{delta:+.1%}**", ""]

    if results["rrf"]["misses"]:
        lines += ["## Misses (rrf, not in top 5)", ""]
        lines += [f"- `{m}`" for m in results["rrf"]["misses"]]
        lines.append("")

    (ROOT / "RETRIEVAL.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {ROOT / 'RETRIEVAL.md'}")


if __name__ == "__main__":
    main()
