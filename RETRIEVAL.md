# Retrieval benchmark

Corpus `unverified-f7022b21dc` — 104 chunks, 78 hand-labelled queries.

Queries are written to imitate real hospital bill line printing and are deliberately not copied from the corpus alias lists, so this measures generalisation rather than lookup.

| strategy | recall@1 | recall@3 | recall@5 |
|---|---|---|---|
| bm25 | 79.5% | 88.5% | 88.5% |
| dense | 85.9% | 89.7% | 93.6% |
| rrf | 85.9% | 94.9% | 97.4% |

RRF vs best single strategy at k=5: **+3.8%**

## Misses (rrf, not in top 5)

- `'MEAL CHARGES RELATIVE' -> expected ['L1-009'], got ['L1-011', 'L1-014', 'L2-011']`
- `'CRUTCH AXILLA PAIR' -> expected ['L1-029'], got ['L3-018', 'L2-002', 'L3-003']`
