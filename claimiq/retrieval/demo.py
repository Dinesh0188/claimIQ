"""Show what each retrieval strategy returns for a query, side by side.

    python -m claimiq.retrieval.demo "STRL GLV 7.5"
"""

from __future__ import annotations

import sys

from claimiq.retrieval.corpus import corpus_version
from claimiq.retrieval.search import get_index


def main(query: str) -> None:
    index = get_index()
    print(f"\nquery: {query!r}")
    print(f"corpus: {len(index.chunks)} chunks, version {corpus_version()}")
    print(f"dense retrieval: {'available' if index.dense_available else 'UNAVAILABLE (BM25 only)'}\n")

    for strategy in ("bm25", "dense", "rrf"):
        print(f"--- {strategy} ---")
        hits = index.search(query, k=5, strategy=strategy)
        if not hits:
            print("  (no hits)")
        for rank, hit in enumerate(hits, 1):
            ranks = f"bm25#{hit.bm25_rank if hit.bm25_rank is not None else '-'}" \
                    f" dense#{hit.dense_rank if hit.dense_rank is not None else '-'}"
            print(
                f"  {rank}. {hit.chunk.chunk_id:<10} {hit.chunk.title[:46]:<46} "
                f"[{hit.chunk.list_name or hit.chunk.meta.get('topic', '-')}]  {ranks}"
            )
        print()


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]) or "sterile gloves OT")
