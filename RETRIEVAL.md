# Retrieval benchmark

Corpus `unverified-bc23b2c89a` — 104 chunks, 78 hand-labelled queries.

Queries are written to imitate real hospital bill line printing and are deliberately not copied from the corpus alias lists, so this measures generalisation rather than lookup.

| strategy | recall@1 | recall@3 | recall@5 |
|---|---|---|---|
| bm25 | 78.2% | 87.2% | 87.2% |
| dense | 84.6% | 88.5% | 92.3% |
| rrf | 89.7% | 93.6% | 96.2% |

RRF vs best single strategy at k=5: **+3.8%**

## Misses (rrf, not in top 5)

- `'MEAL CHARGES RELATIVE' -> expected ['L1-009'], got ['L1-011', 'L1-014', 'L2-011']`
- `'CRUTCH AXILLA PAIR' -> expected ['L1-029'], got ['L3-018', 'L2-002', 'L3-003']`
- `'REGN CHARGES' -> expected ['L4-001'], got ['L1-027', 'L1-014', 'L2-012']`
