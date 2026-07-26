"""Hybrid retrieval: BM25 + dense embeddings, fused with Reciprocal Rank Fusion.

Why both: bill lines print things like "STRL GLV 7.5". BM25 nails the exact token
when it survives ("gauze", "drape"); dense embeddings handle the paraphrase that
BM25 misses ("hand rub" -> "hand wash"). RRF combines them without needing a tuned
weight, because it fuses *ranks* rather than incomparable score scales.

No vector database. A few hundred chunks is a numpy dot product -- adding a service
here would be theatre.
"""

from __future__ import annotations

import re
from functools import lru_cache

import numpy as np
from pydantic import BaseModel
from rank_bm25 import BM25Okapi

from claimiq.config import ROOT
from claimiq.retrieval.corpus import Chunk, corpus_version, load_corpus

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RRF_K = 60  # standard damping constant; ranks past ~60 contribute little

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class Hit(BaseModel):
    chunk: Chunk
    score: float
    bm25_rank: int | None = None
    dense_rank: int | None = None
    # Raw cosine similarity. Unlike the fused rank score this is calibrated and
    # comparable across queries, so it is what the confidence gate keys on.
    cosine: float = 0.0


def _embedding_cache_path() -> object:
    cache_dir = ROOT / ".cache" / "embeddings"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{corpus_version()}.npy"


class HybridIndex:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self._bm25 = BM25Okapi([tokenize(c.search_text) for c in chunks])
        self._vectors = self._load_vectors()
        self._embedder = None

    # --- dense side --------------------------------------------------------

    def _load_vectors(self) -> np.ndarray | None:
        """Embeddings are cached against the corpus hash, so editing the corpus
        invalidates them automatically and nothing goes stale silently."""
        path = _embedding_cache_path()
        if path.exists():
            return np.load(path)

        try:
            from fastembed import TextEmbedding
        except ImportError:
            return None

        try:
            model = TextEmbedding(EMBED_MODEL)
            vectors = np.array(list(model.embed([c.search_text for c in self.chunks])))
        except Exception:
            # Dense retrieval is a bonus, not a hard dependency. BM25 still works.
            return None

        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        np.save(path, vectors)
        return vectors

    def _embed_query(self, query: str) -> np.ndarray | None:
        if self._vectors is None:
            return None
        if self._embedder is None:
            from fastembed import TextEmbedding

            self._embedder = TextEmbedding(EMBED_MODEL)
        vector = np.array(next(iter(self._embedder.query_embed([query]))))
        return vector / np.linalg.norm(vector)

    @property
    def dense_available(self) -> bool:
        return self._vectors is not None

    # --- ranking -----------------------------------------------------------

    def _bm25_order(self, query: str) -> list[int]:
        scores = self._bm25.get_scores(tokenize(query))
        return [i for i in np.argsort(scores)[::-1] if scores[i] > 0]

    def _dense_order(self, query: str) -> tuple[list[int], np.ndarray | None]:
        vector = self._embed_query(query)
        if vector is None:
            return [], None
        similarities = self._vectors @ vector
        return list(np.argsort(similarities)[::-1]), similarities

    def search(self, query: str, k: int = 5, strategy: str = "rrf", pool: int = 25) -> list[Hit]:
        """strategy: 'rrf' | 'bm25' | 'dense'. The alternatives exist so the README
        can report what fusion actually bought, rather than asserting it helped."""
        bm25 = self._bm25_order(query)[:pool]
        if strategy == "bm25":
            dense, similarities = [], None
        else:
            dense_all, similarities = self._dense_order(query)
            dense = dense_all[:pool]

        if strategy == "bm25":
            ordered = [(i, 1.0 / (RRF_K + r)) for r, i in enumerate(bm25)]
        elif strategy == "dense":
            ordered = [(i, 1.0 / (RRF_K + r)) for r, i in enumerate(dense)]
        else:
            fused: dict[int, float] = {}
            for ranking in (bm25, dense):
                for rank, idx in enumerate(ranking):
                    fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank)
            ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)

        bm25_pos = {idx: r for r, idx in enumerate(bm25)}
        dense_pos = {idx: r for r, idx in enumerate(dense)}

        return [
            Hit(
                chunk=self.chunks[idx],
                score=score,
                bm25_rank=bm25_pos.get(idx),
                dense_rank=dense_pos.get(idx),
                cosine=float(similarities[idx]) if similarities is not None else 0.0,
            )
            for idx, score in ordered[:k]
        ]


@lru_cache(maxsize=1)
def get_index() -> HybridIndex:
    return HybridIndex(load_corpus())
