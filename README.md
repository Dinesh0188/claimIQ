# ClaimIQ

**Catch the deduction before the insurer does.**

An agentic auditor for Indian hospital insurance claims. Upload a bill, and a
10-node LangGraph agent reads it with a vision model, grounds every determination in
a retrieved rule corpus, calls a deterministic calculator for the money, checks its
own work, and hands back three numbers the billing desk actually needs:

| | |
|---|---|
| **Estimated settlement** | what the insurer will likely pay |
| **Patient liability** | what the family owes, knowable *at admission* rather than at the discharge counter |
| **Hospital write-off** | what the hospital is silently losing on **every claim it files** |

That third number is the point. Most tools stop at "these items are non-payable."
The non-payable framework is four lists with four different **bearers** — List I is the
patient's cost, Lists II/III/IV are a hospital billing error that repeats forever. Only
the second finding is worth money to the hospital, and a blocklist cannot surface it.

> **Estimates, not adjudications.** ClaimIQ estimates likely deductions under
> configurable insurer rules. It does not predict any specific adjudicator's decision.
> The rule corpus is an **unverified snapshot** and has not been checked against current
> IRDAI circulars. All data in this repo is **synthetic**.

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Add a free [Groq key](https://console.groq.com/keys) to `.env`, then:

```bash
python scripts/index_corpus.py
python scripts/gen_samples.py 300
python scripts/gen_bill_pdf.py
python scripts/seed_db.py
.\start.ps1
```

API on `:8000`, UI on `:8501`. **It runs with no key at all** — set `AI_ENABLED=false`
and every screen still works on the deterministic engine.

---

## Measured results

Regenerate all of these; none is hand-typed.

**Retrieval** — `python scripts/bench_retrieval.py` → [RETRIEVAL.md](RETRIEVAL.md)

| strategy | recall@1 | recall@5 |
|---|---|---|
| BM25 only | 78.2% | 87.2% |
| Dense only | 84.6% | 92.3% |
| **RRF hybrid** | **89.7%** | **96.2%** |

**Classification** — `python scripts/bench_classify.py --llm` → [CLASSIFICATION.md](CLASSIFICATION.md)

| strategy | accuracy | **non-payable recall** | false deduction rate |
|---|---|---|---|
| `head_only` control | 51.7% | 30.5% | 3.6% |
| keyword table | 73.6% | 61.0% | 0.0% |
| retrieval + threshold | 40.2% | 11.9% | 0.0% |
| deterministic (both) | 74.7% | 62.7% | 0.0% |
| **grounded LLM** (`gpt-oss-120b`) | 96.6% | **96.6%** | 3.6% |
| grounded LLM (`gemma-4-26b`) | 100.0% | 100.0% | 0.0% |

Two models are listed because the result was not the one I expected: the small free
Gemma 4 26B beat the much larger `gpt-oss-120b` on this task, and did it with no false
deductions. Worth remembering before reaching for the biggest available model — on a
narrow, well-grounded classification task the extra capacity bought nothing here.

**Read `accuracy` against the `head_only` control, not on its own.** That control ignores
the item description entirely and guesses from the billing head — and it does well because
of how this labelled set is built: all 59 non-payable items carry `head=OTHER`, so the head
column nearly determines the payable/non-payable split by itself. A headline of "100%
accuracy" would be mostly an artifact.

**`non-payable recall` is the honest column.** It measures assigning the correct list among
four — I, II, III or IV — which the head cannot indicate at all. The LLM's real contribution
is **62.7% → 100%** there. *false deduction rate* is the expensive error: a real medical
charge wrongly disallowed.

**Bill extraction** — `python scripts/bench_extract.py` → [EXTRACTION.md](EXTRACTION.md)

| path | when it runs | row recall | amount accuracy |
|---|---|---|---|
| pdfplumber table parse | native PDF with a text layer | **100%** | 100% |
| vision model | scans with no text layer | 81.1% | 100% |

Also `scripts/run_eval.py` → [EVAL.md](EVAL.md).

---

## Why the LLM does not do arithmetic

The agent calls a deterministic `Decimal` calculator for every rupee, and the model
only describes numbers it was handed.

This is not a limitation worked around — it is what makes the **verifier** possible.
Because the arithmetic is deterministic, a disagreement between the model's narrative
and the tool's output is *detectable*, so the verifier detects it and re-prompts. If the
model produced the numbers there would be nothing to reconcile against.

> The model has judgment; the tool has precision.

The invariant, asserted on every sample in the test suite:

```
projected_settlement + patient_liability + hospital_writeoff == gross_bill
```

## Why there is an LLM at all

Because the cheap approach was measured and it does not work. Cosine similarity cannot
separate payable from non-payable — "OT charges" (payable) scores **0.756** against the
catalog while "STRL GLV 7.5" (non-payable) scores **0.591**. Sweeping the threshold only
trades one error for the other:

| threshold | non-payable recall | real charges wrongly disallowed |
|---|---|---|
| 0.65 | 79.7% | **50.0%** |
| 0.70 | 69.5% | 28.6% |
| 0.82 | 62.7% | 0.0% |

The LLM is what recovers the recall without paying that price.

---

## Architecture

```
 [1] INGEST      PDF → text layer, or page images if there isn't one
 [2] EXTRACT     table parse when possible, vision LLM when not
 [3] NORMALIZE   abbreviation expansion, dedupe
 [4] CLASSIFY    BM25 + dense + RRF → LLM verdict, must cite a chunk
 [5] RETRIEVE    policy clauses for this claim
 [6] REASON      grounded coverage determination
 [7] COMPUTE ◄── TOOL: deterministic waterfall, 3 insurer profiles
 [8] VERIFY      invariant? citations? do the narrative's numbers exist?
       ╰─ fail ─→ REPAIR (bounded, max 2) ─┐
 [9] EXPLAIN ◄───────────────────────────  ┘
[10] REPORT      audit PDF + persist for the portfolio dashboard
```

The backward edge from `verify` is why this is a graph and not a chain.

**Every node is a plain function `ClaimState -> dict`.** Nothing in `nodes/` imports
langgraph, so the framework is swappable without touching a node.

Full write-up, stack rationale, challenges and interview prep:
**[DOCUMENTATION.md](DOCUMENTATION.md)**

---

## Screens

| Screen | What it shows |
|---|---|
| **Audit** | Waterfall from gross bill to settlement, line-item verdicts with citations, missing documents, room-downgrade simulator, PDF export |
| **Trace** | Each node's latency, tokens, cache hits — and the repair loop firing |
| **Dashboard** | Portfolio leakage: preventable loss by cause, top 10 billing mistakes, most-missed documents |
| **Ask** | Plain-English questions → validated read-only SQL |

---

## Honest limitations

- **The corpus is unverified.** Compiled from publicly circulated non-payable lists,
  not checked against current IRDAI circulars. `verified_on` is `null` and the UI says so.
- **Insurer behaviour varies.** Which heads count as "associated charges" for the
  proportionate deduction differs between insurers, so output is a **range** across three
  profiles, never a single confident number.
- **All data is synthetic.** Benchmarks measure generalisation on hand-labelled synthetic
  strings. Real hospital bills will score differently.
- **The round-number check fires on our own samples** — synthetic bills use round figures.
  A real-data artifact of a synthetic-data project, left visible rather than tuned away.
- **LLM-as-judge scores are directional**, not measurements. They gate nothing; the
  deterministic checks do.
- **It predicts, it does not adjudicate.** Output is a pre-submission checklist for a human.

## Tests

```bash
python -m pytest -q
```

51 tests, ~12s, no API key needed — the suite forces `AI_ENABLED=false` so it is
offline and deterministic.
