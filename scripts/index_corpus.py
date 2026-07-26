"""Build the embedding index. Run once after editing the corpus.

    python scripts/index_corpus.py

Embeddings are keyed by a hash of the corpus content, so this is idempotent and a
corpus edit invalidates the old vectors automatically.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claimiq.retrieval.corpus import corpus_version, load_corpus  # noqa: E402
from claimiq.retrieval.search import get_index  # noqa: E402


def main() -> None:
    chunks = load_corpus()
    print(f"corpus version : {corpus_version()}")
    print(f"chunks         : {len(chunks)}")

    by_list = Counter(c.list_name or c.meta.get("topic", "policy") for c in chunks)
    for name, count in sorted(by_list.items()):
        print(f"  {name:<22} {count}")

    print("\nbuilding embeddings (first run downloads the model, ~130 MB) ...")
    index = get_index()
    if index.dense_available:
        print(f"dense index ready: {index._vectors.shape[0]} vectors x {index._vectors.shape[1]} dims")
    else:
        print("WARNING: dense embeddings unavailable — retrieval will run BM25-only.")


if __name__ == "__main__":
    main()
