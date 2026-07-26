# ClaimIQ — Evaluation & Benchmark Report

How every AI component in this system is measured, how the datasets were built, what the
numbers mean, and — the part most evaluation write-ups skip — the three occasions the
measurement itself was wrong and how that was discovered.

Raw generated results live in [RETRIEVAL.md](RETRIEVAL.md),
[CLASSIFICATION.md](CLASSIFICATION.md), [EXTRACTION.md](EXTRACTION.md) and
[EVAL.md](EVAL.md). Every figure below is reproduced from those files, never written from
memory.

---

## 1. What is measured, and how hard

The system has four AI-touching components and two deterministic guardrails. They are not
evaluated the same way, because they do not fail the same way.

| Component | Method | Ground truth | Gates output? |
|---|---|---|---|
| Retrieval | recall@k on labelled queries | hand-labelled, 78 queries | no |
| Classification | accuracy + recall + false-deduction rate | hand-labelled, 87 items | no |
| Extraction | field-level diff against source | **exact** — PDFs generated from JSON | no |
| Explanation | LLM-as-judge, 1–5 | none — subjective | **no, deliberately** |
| Arithmetic invariant | equality assertion | exact | **yes** |
| Citation coverage | set membership against corpus | exact | **yes** |

The two things that gate output are the two that are deterministic. Nothing subjective can
block a result, and nothing an LLM says about quality changes what ships.

---

## 2. Dataset construction

### Retrieval — 78 labelled queries

`data/eval/retrieval_queries.json`. Each entry maps a query to a list of acceptable
`chunk_id`s.

The critical design constraint: **queries are deliberately not copied from the corpus alias
lists.** Had they been, retrieval would score near 100% and the number would measure lookup,
not generalisation. Instead they imitate real bill printing:

```json
{"query": "STRL GLV 7.5",   "expected": ["L3-002"]}
{"query": "ATTENDER FOOD CHRG", "expected": ["L1-009"]}
{"query": "HK CHARGES",     "expected": ["L2-011"]}
```

`expected` is a list because some bill lines legitimately map to more than one entry — a
lumbo-sacral belt is defensibly either L1-004 or L1-034. Forcing a single gold answer would
penalise correct behaviour.

A test (`test_every_benchmark_query_targets_a_real_chunk`) asserts every expected ID exists,
so the benchmark cannot silently rot when the corpus changes.

### Classification — 87 labelled items

`data/eval/classification_items.json`. 28 payable, 59 non-payable across Lists I–IV, each
with `description`, `head` and `expected`.

### Extraction — exact ground truth

The only component with *perfect* ground truth, and only because of how it was built:
`scripts/gen_bill_pdf.py` renders bills **from** the JSON packets. The correct answer is
therefore known exactly, and `--scan` re-renders the same bills as image-only PDFs with the
text layer destroyed, so the OCR and vision paths can be measured against the same truth.

An extraction benchmark without ground truth is a vibe. This one is measurable because the
generator and the evaluator share a source.

### Threats to validity — stated up front

- **All data is synthetic.** Real bills have inconsistent naming, merged cells, and
  hospital-specific abbreviations. Numbers here are upper bounds.
- **Single annotator.** I wrote both the corpus and the labels. No inter-annotator
  agreement, so systematic misunderstanding of the domain would be invisible.
- **The corpus is unverified** against current IRDAI circulars.
- **Label/corpus coupling.** Labels were written by someone who knew the corpus. Mitigated
  for retrieval by avoiding alias reuse; not fully eliminable.

---

## 3. Retrieval results

From [RETRIEVAL.md](RETRIEVAL.md), 104 chunks, 78 queries:

| strategy | recall@1 | recall@3 | recall@5 |
|---|---|---|---|
| BM25 | 79.5% | 88.5% | 88.5% |
| dense | 85.9% | 89.7% | 93.6% |
| **RRF** | 85.9% | **94.9%** | **97.4%** |

RRF beats the best single strategy by **+3.8pp at k=5** and +5.1pp at k=3. At k=1 it
**ties** dense at 85.9% — fusion helps you find the right chunk within the candidate set
handed to the classifier, and does not improve the single best guess. Since the classifier
sees five candidates, k=5 is the metric that matters operationally, but the tie at k=1 is
worth stating rather than quietly reporting only the flattering column.

Two queries still miss at k=5, both instructive: `MEAL CHARGES RELATIVE` (retrieves laundry
and telephone — "relative" is not in any alias) and `CRUTCH AXILLA PAIR` (retrieves surgical
bundles — "axilla" reads as anatomical). Both are alias-coverage gaps, fixable by editing
the corpus rather than by changing the retriever.

The pre-registered decision rule was: *if fusion does not beat both single strategies, keep
the simpler one.* It won, so hybrid stays. Had it lost, `search.py` would be BM25-only
today — the `strategy` parameter exists precisely so this stays checkable rather than
becoming an assumption.

Why it wins is visible in the failure modes: BM25 alone misses `hand rub` → `hand wash`
(no shared token); dense alone blurs `CSSD` and `MRD` into generic administrative text.
Neither is uniformly better, which is exactly the condition where rank fusion helps.

---

## 4. Classification results

From [CLASSIFICATION.md](CLASSIFICATION.md), 87 items:

| strategy | accuracy | **non-payable recall** | false deduction rate | citation rate |
|---|---|---|---|---|
| `head_only` control | 51.7% | 30.5% | 3.6% | 0% |
| keyword table | 73.6% | 61.0% | 0.0% | 0% |
| retrieval + threshold | 40.2% | 11.9% | 0.0% | 100% |
| deterministic (both) | 74.7% | 62.7% | 0.0% | 3% |
| **LLM** (`gpt-oss-120b`) | 96.6% | **96.6%** | 3.6% | 100% |
| LLM (`gemma-4-26b`) | 100.0% | 100.0% | 0.0% | 100% |

### Metric definitions

- **accuracy** — exact match on the 6-way label. *Inflated; see §5.*
- **non-payable recall** — of the 59 genuinely non-payable items, the share assigned the
  **correct list among four**. The headline metric.
- **false deduction rate** — of the 28 genuinely payable items, the share wrongly marked for
  deduction. The expensive error: it produces a bill the patient should never have seen.
- **citation rate** — of all LIST_* verdicts, the share carrying a valid corpus chunk ID.

### Why `non-payable recall` is the headline and accuracy is not

Because accuracy is compromised, and §5 explains how. Recall measures the four-way list
assignment, which the leak cannot help with.

### Asymmetric errors

Missing a non-payable item costs the hospital a recovery opportunity. Wrongly disallowing a
real charge produces a bill that should never have existed. These are not equally bad, so
the retrieval confidence gate sits at cosine 0.82 — the point where false deductions hit
zero — rather than at 0.65 where recall peaks at 79.7% and **50% of real medical charges get
disallowed**.

### The cheap approaches, measured before reaching for a model

Threshold sweep on the retrieval-only classifier:

| threshold | accuracy | non-payable recall | payable wrongly deducted |
|---|---|---|---|
| 0.65 | 70.1% | 79.7% | **50.0%** |
| 0.70 | 70.1% | 69.5% | 28.6% |
| 0.82 | 74.7% | 62.7% | **0.0%** |

There is no threshold that gets both. `OT charges` (payable) scores 0.756 against the
catalog while `STRL GLV 7.5` (non-payable) scores 0.591 — the distributions overlap, so no
single cut separates them. **That is the measured argument for the LLM**, and it is a much
better answer than "it is an AI project so it has AI in it."

### Cross-model: the small model won

`gemma-4-26b` (free, via OpenRouter) scored 100%/100%/0% against `gpt-oss-120b`'s
96.6%/96.6%/3.6%. On a narrow task where retrieval already supplies the candidates, the
larger model bought nothing and cost false deductions. Both rows are published rather than
quietly reporting the better one.

---

## 5. Failure analysis

### 5.1 A 100% score that was mostly dataset leakage

After a prompt fix instructing the model to treat the billing `head` as decisive, the
classifier scored 100% on everything. A perfect score on a messy real-world task is grounds
for suspicion, so the dataset got audited:

```
PAYABLE items by head:      ROOM 3, NURSING 1, PROCEDURE 9, INVESTIGATION 4,
                            PHARMACY 4, IMPLANT 3, CONSULTATION 3, OTHER 1
NON-PAYABLE items by head:  OTHER 59

head-only baseline: 86/87 = 98.9%
```

All 59 non-payable items carry `head=OTHER`. Only *Ambulance charges* breaks the pattern. A
rule of "head is not OTHER → payable", knowing nothing about the item, splits payable from
non-payable at **98.9%**. Telling the model the head was decisive handed it the binary
answer and then congratulated it.

**Resolution.** `head_only` is now a permanent control row in the benchmark. It scores 51.7%
accuracy, which immediately tells any reader how much of everyone else's accuracy is free.
And the headline moved to non-payable recall, which the head cannot indicate: control 30.5%,
best deterministic 62.7%, LLM 96.6%. *That* gap is real.

Worth noting the head is legitimate signal — real bills do carry a department column. The
flaw is that in synthetic data I assigned those heads myself, so they separate far more
cleanly than they ever would in practice.

### 5.2 The verifier that cried wolf

The narrative reconciliation check extracts numbers from the model's prose and asserts each
appears in the engine's output. Initially it failed on half the scenarios and exhausted both
repair attempts — every failure a false positive. It was flagging figures the model had
derived correctly by subtraction (a pre-auth variance) that were simply not in the fact set
as standalone values.

**Resolution.** The facts were extended to include derived quantities the narrative
legitimately needs, and the reconciliation floor was raised so "8 days" and "10% co-pay"
are not treated as money. Now 4/4 pass with **0 repairs**. A guardrail that fires constantly
gets ignored, which makes it worse than no guardrail.

### 5.3 The judge scored 1.2/5 on correct output

The most instructive failure. After the verifier was fixed, the LLM judge reported mean
groundedness of **1.2/5** — claiming the narratives were full of invented figures. The
deterministic verifier had passed every one.

One of them had to be wrong. The judge's own list of "unsupported" claims settled it:

- *"loss of INR 24,300"* — the hospital write-off, straight off the waterfall
- *"Single AC at INR 12,000/day against a cap of INR 6,000/day"* — both supplied
- *"exceeds the pre-authorised amount by INR 60,000"* — supplied

All grounded. The bug was in the harness: `judge_explanation` rebuilt its own compact summary
instead of using the fact set the writer received, silently dropping the room rate, the
pre-auth figures and the flag text. **The judge was marking statements unsupported because
it could not see the evidence.**

Passing the identical `facts_json` moved groundedness from **2.7 → 4.5/5** with no change to
model or prompt. A second round of complaints (*"patient gown is covered under room tariff"*)
revealed the same bug one level deeper — the facts carried each item's classification but
not its rationale — and adding `reason` and `cited_rule` closed it.

Two conclusions:

1. **Judging against less evidence than the writer had measures your harness, not your
   model.** A broken eval is more dangerous than no eval, because it looks like data.
2. **The deterministic check and the LLM judge disagreed, and the deterministic one was
   right.** That is the whole argument for keeping the gates mechanical.

### 5.4 Vision hit a quota ceiling, not a capability ceiling

Scanned extraction failed with 413 on every page: the vision model is billed a flat **11,111
tokens per image** against an **8,000 tokens/minute** cap. One image costs more than the
entire per-minute allowance.

The instinct is to shrink the image. Render scale dropped from 2.0 to 1.4 — a little over
half the pixels — and the provider reported *exactly 11,111 tokens again*. Billing is flat
per image, independent of resolution. No downscaling gets under the bar. Page-slicing was
tried separately and made recall worse (22% → 10%), because cropping destroys the table
context that tells the model what a column means.

**Resolution: route around it.** Native PDFs parse deterministically with `pdfplumber` at
**100% row recall, 100% amount accuracy, exact bill totals**. Scans go to local RapidOCR —
free, offline, no torch, no system binary — which turns a page into ~1,500 text tokens the
ordinary chat model structures comfortably inside the free tier.

The deterministic path was not merely cheaper, it was **more accurate**: 100% against the
81% the vision model managed on the one bill that fit inside the quota. Do not quote that
81% as reproducible — it was one bill in a window where the budget happened to allow it.

---

## 6. End-to-end evaluation

From [EVAL.md](EVAL.md), full agent over 4 canonical scenarios:

| scenario | invariant | citations | verifier | repairs | grounded | useful |
|---|---|---|---|---|---|---|
| billing_error | PASS | 100% | pass | 0 | 4/5 | 5/5 |
| cardiac | PASS | 100% | pass | 0 | 4/5 | 5/5 |
| clean | PASS | 100% | pass | 0 | 5/5 | 2/5 |
| incomplete | PASS | 100% | pass | 0 | 5/5 | 5/5 |

- Invariant held **4/4**
- Citation coverage **100%**
- Verifier passed **4/4** with **0** repairs
- Groundedness **4.5/5**, usefulness **4.2/5**
- Mean **13,934 ms**, **2,424 tokens** per claim

`clean` scoring 2/5 on usefulness is correct behaviour, not a defect: a claim with no
problems has little to advise. A metric that rewarded inventing advice for a clean claim
would be the wrong metric.

### Why the judge gates nothing

It shares a model family with the system it grades, which is circular. Its scores swung
1/5 → 5/5 across runs on similar output. And §5.3 showed it confidently wrong about output
the deterministic checker had already validated.

It is kept because it is genuinely useful for one thing: catching a **regression between
prompt versions**. It is not evidence of absolute quality, and it blocks nothing.

A judge call that fails now records `None`, not `0` — scoring an unreachable judge as the
worst possible grade silently defames the output and poisons the mean.

---

## 7. Reproducing all of it

```bash
python scripts/index_corpus.py       # build embeddings (cached by corpus hash)
python scripts/bench_retrieval.py    # → RETRIEVAL.md
python scripts/bench_classify.py     # → CLASSIFICATION.md  (add --llm for model rows)
python scripts/gen_bill_pdf.py --scan
python scripts/bench_extract.py      # → EXTRACTION.md
python scripts/run_eval.py           # → EVAL.md
python -m pytest -q                  # 59 tests, AI off, deterministic
```

Tests run with `AI_ENABLED=false` (`tests/conftest.py`) so the suite is fast, offline and
runnable by anyone who clones the repo without a key. LLM behaviour is measured by the
benchmark scripts, where non-determinism is the subject rather than a nuisance.

Every number in the README and in this document is regenerated by these scripts. None is
typed by hand — which is the only way a documented figure stays true.

---

## 8. What can honestly be concluded

**Supported by this evidence:**

- Hybrid retrieval beats either strategy alone on this corpus (+3.8pp recall@5)
- No non-LLM classifier reaches useful recall without disallowing real charges; the LLM
  raises correct-list assignment from 62.7% to 96.6% at comparable false-deduction cost
- Deterministic table parsing beats vision on native PDFs, and is free
- The arithmetic invariant and citation coverage hold on every scenario tested
- A smaller free model matched or beat a larger one on this task

**Not supported, and not claimed:**

- Any accuracy figure transferring to real hospital bills — the data is synthetic and the
  `head` field is unrealistically clean
- The corpus being correct — it is an unverified snapshot
- Judge scores meaning anything in absolute terms
- Vision extraction being production-ready — it has never completed a full bill within quota
