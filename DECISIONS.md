# ClaimIQ — Engineering Decision Log

Fourteen decisions that shaped this system. Each records the problem, what else was
considered, what was chosen, the trade-off accepted, and what actually happened afterwards.

These are the decisions where the reasoning matters more than the choice. Picking FastAPI
over Flask is not in here — it decided nothing.

A note on format: several of these were *revised* after measurement contradicted the
original reasoning. Those revisions are kept visible rather than rewritten, because a
decision log that only records decisions that turned out well is a marketing document.

---

## ADR-001 — The LLM does not do arithmetic

**Problem.** A claim auditor computes money. Language models are unreliable at multi-step
decimal arithmetic, but the project is explicitly meant to be AI-first, and moving the math
out of the model looks like moving AI out of the project.

**Alternatives.**
1. Model computes everything — maximal "AI-ness", unverifiable output
2. Model computes, code spot-checks — the check needs its own arithmetic, so this is (3)
   with extra steps
3. Model reasons and calls a deterministic calculator as a tool

**Chosen.** (3). `tools/waterfall.py` computes in `Decimal`; the model decides *which* rules
apply and explains the result.

**Reasoning.** This is not a retreat from AI-first — it is what makes the guardrail possible.
Because the arithmetic is deterministic and reproducible, disagreement between the model's
narrative and the tool's output is *detectable*. `verify_node` detects it and re-prompts. Had
the model produced the numbers, "did it hallucinate?" would be unanswerable.

**Trade-off.** Rules the calculator does not implement cannot be applied, however well the
model reasons about them. The waterfall is a fixed six steps.

**Consequence.** Enabled every other guardrail in the system. It is also the single
strongest interview answer: *the model has judgement, the tool has precision, and giving the
agent a calculator is why the verifier can catch it.*

---

## ADR-002 — Rules are a RAG corpus, not a lookup table

**Problem.** The IRDAI non-payable framework needs encoding. The obvious shape is a
dictionary from item name to list.

**Alternatives.**
1. YAML/dict lookup with fuzzy matching
2. Markdown corpus, chunked, embedded, retrieved

**Chosen.** (2). 104 hand-written chunks in `corpus/`, each with a stable `chunk_id`.

**Reasoning.** A lookup table can say *what* an item is; it cannot hand a hospital the
sentence justifying a deduction. Making rules retrievable text means every determination
cites a chunk that a billing manager can be shown. It also makes the rulebook data rather
than code — a new circular is a corpus edit and a version bump, not a deploy.

**Trade-off.** Retrieval can miss. A dictionary lookup either hits or does not; retrieval
returns a nearest neighbour that may be wrong, which is why the confidence gate and the
citation check exist.

**Consequence.** `chunk_id` became the backbone of grounding. `GET /api/rules/{chunk_id}`
lets the Trace view expand any citation into source text — grounding you cannot inspect is
a claim, not a guarantee.

---

## ADR-003 — SQLite, not Postgres

**Problem.** The portfolio dashboard needs persistence across ~300 claims.

**Alternatives.** SQLite · Postgres (hosted) · DuckDB · Parquet files

**Chosen.** SQLite.

**Reasoning.** 302 rows, one writer, read-heavy embedded queries. Postgres adds a network
hop, a connection pool, a hosting dependency and a connection string in secrets — to solve a
problem that does not exist at this scale. `store.py` is the only module touching SQL, so
migration stays contained if the shape of the problem changes.

**Trade-off, stated honestly.** Recruiters keyword-scan for "PostgreSQL". This choice costs
that, deliberately. The mitigation is to make the reasoning visible: a documented decision
reads as more senior than an unexamined default.

**Consequence.** Deployment needs no database service at all — the DB is a committed file,
which is also what makes Streamlit Cloud's ephemeral disk a non-issue.

---

## ADR-004 — numpy, not a vector database

**Problem.** Dense retrieval needs vector similarity.

**Alternatives.** ChromaDB · FAISS · LanceDB · `sqlite-vec` · raw numpy

**Chosen.** numpy. `self._vectors @ query_vector` plus an argsort.

**Reasoning.** 104 chunks × 384 dims is a 160 KB array; the matrix multiply is microseconds.
ChromaDB drags in onnxruntime and pydantic pins that fight the rest of the stack, plus a
persistence lifecycle, to replace about twenty lines. That is an abstraction for single-use
code.

**Trade-off.** Brute force is O(n·d) per query and stops being free somewhere around 50k
chunks.

**Consequence.** Zero dependency-resolution problems in the retrieval layer — which mattered
more than expected, because the stack already carries onnxruntime via fastembed and a second
copy would have been a real conflict.

---

## ADR-005 — fastembed, not sentence-transformers

**Problem.** Local embeddings without an API call.

**Chosen.** `fastembed` with BGE-small (ONNX).

**Reasoning.** Same embedding quality, ~130 MB instead of ~2 GB, and **no PyTorch**. A
portfolio project that requires a 2 GB download before it runs will not be run by the person
evaluating it.

**Consequence.** This decision paid off twice. It kept setup light, and the onnxruntime it
installs is exactly what made ADR-011's local OCR free.

---

## ADR-006 — LangGraph, with nodes that do not know about it

**Problem.** The pipeline needs a conditional backward edge (verify → explain, bounded).

**Alternatives.** Hand-rolled loop · LangGraph · LangChain LCEL

**Chosen.** LangGraph, with a hedge: every node is a plain `ClaimState -> dict` function and
**nothing in `nodes/` imports langgraph**. The framework appears only in `graph.py`.

**Reasoning.** For a purely linear pipeline I would not have used it. The justification is
the cycle: a chain cannot express "check, and if wrong re-prompt specifically, but give up
after two." That is genuinely what LangGraph is for. The hedge exists because the framework
is the least stable dependency in the stack.

**Trade-off.** A dependency with real churn, for one graph feature.

**Consequence.** Swapping to a hand-rolled loop remains a ~30 line change touching zero
nodes. The hedge has not been needed, but it made adopting the framework a low-risk decision
rather than a bet.

---

## ADR-007 — Provider profiles in a file, not environment variables

**Problem.** Switching LLM provider means changing four related values together — base URL,
key, chat model, vision model — plus a capability flag.

**Alternatives.** Four env vars · a profile file · a hosted config service

**Chosen.** `providers.json`, gitignored, with `providers.example.json` committed.

**Reasoning.** Four environment variables that must move in lockstep is a footgun; one named
profile is not. It also lets the UI offer a live switch. Crucially, `supports_json_mode` is
declared per provider — not every OpenAI-compatible endpoint implements `response_format`,
and sending it to one that does not rejects the entire request.

**Consequence.** This stopped being theoretical twice in one session: when the Groq daily
quota ran out, and again when Groq began returning 403 for the entire network. Failing over
to OpenRouter was a dropdown, not a code change. The capability flag earned its place
immediately — the free Gemma model does not support JSON mode, and without the flag every
call would have failed.

---

## ADR-008 — Citations are required of the model, not of hand-written code

**Problem.** Every non-payable determination should cite the corpus chunk justifying it, so
the verifier can reject ungrounded output.

**First attempt.** Require a citation on *every* LIST_* finding.

**What broke.** Keyword-table matches have no chunk to cite. Requiring citations universally
meant every audit in deterministic mode failed verification — the no-key path was
structurally broken by a guardrail meant to constrain the model.

**Revised decision.** `ItemFinding.source` records `keyword | retrieval | llm`, and the
verifier enforces citations **only on `source == "llm"`**.

**Reasoning.** The purpose of the rule is to stop the *model* asserting things it cannot
point at. A hand-written keyword table is already auditable — it is source code, reviewable
in the diff. Demanding it cite a document is ceremony.

**Consequence.** Two regression tests now pin both halves: uncited model output fails,
uncited keyword output passes. The general lesson is that a guardrail scoped too broadly
does not fail loudly — it fails by breaking a legitimate path.

---

## ADR-009 — The confidence gate is set where false deductions hit zero

**Problem.** Retrieval-only classification needs a cosine threshold.

**Data.** Sweeping it:

| threshold | non-payable recall | payable wrongly deducted |
|---|---|---|
| 0.65 | 79.7% | **50.0%** |
| 0.70 | 69.5% | 28.6% |
| 0.82 | 62.7% | **0.0%** |

**Chosen.** 0.82, sacrificing 17pp of recall.

**Reasoning.** The errors are not symmetric. Missing a non-payable item costs the hospital a
recovery opportunity. Wrongly disallowing a real medical charge produces a bill the patient
should never have been shown. When errors differ in cost, the operating point belongs where
the expensive error is eliminated, not where the headline metric peaks.

**Consequence.** The 17pp of lost recall is exactly what the LLM recovers (62.7% → 96.6%),
which is *why* there is an LLM. The threshold sweep is the measured argument for the model —
far better than asserting the model was needed.

---

## ADR-010 — Money is Decimal everywhere, including on disk

**Problem.** Float arithmetic on currency accumulates error and cannot be reasoned about
exactly.

**Chosen.** `Decimal` throughout the engine, **integer paise** in SQLite, rupee VIEWs for
readers.

**Reasoning.** The engine was always `Decimal` and the README made a point of it — then the
persistence layer declared `gross_bill REAL` and threw it away at the last boundary. At 302
rows the aggregate drift is ~1e-10 and invisible in rupee display, so this was a
*consistency* defect rather than a live bug. But "money is never float" is a claim, and a
claim should be true everywhere or not be made.

Rupee views (`v_claims`, `v_findings`) sit on top so reporting and text-to-SQL read natural
units — a generated query returns `24300`, not `2430000` — while storage stays exact. The
text-to-SQL allow-list contains only views, so generated SQL cannot read raw paise and report
a figure 100× too large.

**Trade-off.** A schema migration, and readers must go through views.

**Consequence.** Found by writing the documentation, not by a failing test — the value of a
"database design" section is that it makes you look at the schema. `test_store.py` now
asserts exact round-tripping and that the bucket invariant survives persistence.

---

## ADR-011 — Local OCR, not a vision model, for scans

**Problem.** Scanned bills have no text layer. The vision model is billed a flat ~11,111
tokens per image against an 8,000/minute free-tier cap — one image exceeds the entire
allowance.

**Alternatives.** Pay for a higher tier · shrink the image · slice pages · Tesseract ·
EasyOCR · PaddleOCR · RapidOCR

**Rejected, with evidence.** Shrinking failed: render scale 2.0 → 1.4, half the pixels, and
the provider reported *exactly 11,111 tokens again*. Billing is flat per image, so no
downscaling gets under the bar. Slicing failed differently — recall fell 22% → 10%, because
cropping destroys the table context that tells the model what a column means. Tesseract needs
a system binary; EasyOCR and PaddleOCR need PyTorch.

**Chosen.** RapidOCR on onnxruntime — pip-installable, no binary, no torch, and it runs on
the runtime `fastembed` already installed (ADR-005).

**Reasoning.** OCR turns a page into ~1,500 text tokens, which the ordinary chat model
structures comfortably inside the free tier. Free, offline, and it removes the vision
dependency for the common case.

**Consequence.** The deterministic path is not merely cheaper — it is **more accurate**:
`pdfplumber` gets 100% row recall on native PDFs against the 81% vision managed on the one
bill that fit inside quota. General lesson: when a model-based step hits a wall, check
whether a deterministic path does the job better before optimising the model call.

---

## ADR-012 — The LLM judge gates nothing

**Problem.** Explanation quality is subjective and needs some measurement.

**Chosen.** LLM-as-judge for groundedness and usefulness, reported and explicitly
non-blocking.

**Reasoning.** It shares a model family with the system it grades, which is circular. Its
scores swung 1/5 → 5/5 across runs on similar output.

**The evidence that settled it.** The judge once scored output **1.2/5** for groundedness
that the deterministic verifier had passed. One of them was wrong — and it was the judge. It
was flagging figures like the hospital write-off as invented, because `judge_explanation`
rebuilt its own smaller fact summary instead of using what the writer received. Passing the
identical `facts_json` moved groundedness to 4.5/5 with no change to model or prompt.

**Consequence.** Two design rules. Judging against less evidence than the writer had measures
your harness, not your model — a broken eval is more dangerous than no eval because it looks
like data. And when a deterministic check and an LLM judge disagree, trust the deterministic
one. A failed judge call now records `None`, not `0`, because scoring an unreachable judge as
the worst grade silently defames the output.

---

## ADR-013 — Tests run with AI switched off

**Problem.** The suite took 125 seconds and cost tokens, because tests exercised paths that
called the model.

**Chosen.** `tests/conftest.py` sets `AI_ENABLED=false` before any import.

**Reasoning.** A test suite must be fast, offline, deterministic and runnable by anyone who
clones the repo without a key. Non-deterministic model output in a unit test produces flakes,
and a flaky suite gets ignored.

**Trade-off.** The LLM path is not unit-tested. It is measured by the benchmark scripts
instead, where non-determinism is the subject rather than a nuisance.

**Consequence.** 125 s → 12 s. The `strategy` parameter on `classify_items` exists partly so
tests can pin the deterministic path explicitly rather than depending on a global.

---

## ADR-014 — A control row in the benchmark, after a 100% score turned out to be leakage

**Problem.** The classifier scored 100% accuracy. A perfect score on a messy real-world task
is grounds for suspicion.

**What the audit found.** All 59 non-payable items in the labelled set carry `head=OTHER`;
27 of 28 payable ones do not. A rule of "head is not OTHER → payable", knowing nothing about
the item, separates them at **98.9%**. The prompt fix that told the model the head was
decisive had handed it the answer.

**Chosen.** A permanent `head_only` control strategy in the benchmark, and the headline
metric moved from accuracy to **non-payable recall** — assigning the correct list among
four, which the head cannot indicate.

**Reasoning.** A benchmark that cannot be read against a trivial baseline is not a
benchmark. `head_only` scores 51.7%, which tells any reader immediately how much of
everyone else's accuracy is free. On the honest metric: control 30.5%, best deterministic
62.7%, LLM 96.6%.

**Trade-off.** The headline number dropped from a flattering 100% to a defensible 96.6%.

**Consequence.** The strongest thing in the evaluation, precisely because it is unflattering.
It is also worth noting the head is *legitimate* signal — real bills carry a department
column. The flaw is that in synthetic data I assigned those heads myself, so they separate
far more cleanly than they ever would in practice.

---

## ADR-015 — Table-structure OCR was tried, measured, and rejected

**Problem.** On scans the OCR returns text fragments in reading order, and the model then
has to infer which numbers belong to which bill line. That inference — not character
recognition — is where extraction accuracy is lost. RapidOCR reads 23/23 amounts correctly;
it is the row assembly that is fragile.

**Alternatives considered.**

*PaddleOCR / PP-StructureV3.* Best published accuracy (94.5% on OmniDocBench) and returns
real cell coordinates. A dry-run resolve against this environment showed the true cost:
**34 additional packages and a numpy downgrade from 2.5.1 to 2.3.5**, underneath a green
59-test suite, to improve a path only reached on scanned uploads. Rejected on cost.

*RapidTable (SLANet-plus).* The same structural benefit for **4 packages and no numpy
change**, running on the onnxruntime already present. On paper the obvious choice, and it
was installed and implemented.

**Decision.** Neither. The scan path keeps RapidOCR fragments plus model reassembly.

**Why — the measurement.** RapidTable was fed our own OCR output and asked for the grid.
On `clean.pdf`, a bill with roughly 10 rows and 6 columns — about 60 cells — it returned
**367 cells**. Under half the OCR fragments fell inside any detected cell (36 of 76), and
single bill lines were split across grid rows: `"1 Room charges - Single AC"` landed in one
row while `"Accommodation | 5 | 5,500.00 | 27,500.00"` landed in the next. Cropping to the
table region first did not help; the result was the same at three different crops.

The cause is a domain mismatch rather than a bug. SLANet is trained on academic tables
(PubTabNet). A hospital bill carries a letterhead, a patient-details block and section
headers like "ROOM & BOARD", and the model reads all of that as table structure.

The incumbent it had to beat gets `clean.pdf` **8/8 rows with an exact ₹90,400 total**.
Adopting RapidTable would have made extraction measurably worse.

**Trade-off.** Row assembly stays the weakest link in the scan path, and it is still the
right place to look for accuracy gains.

**Consequence.** `rapid-table` was uninstalled rather than left in `requirements.txt`
unused. A dependency kept "in case" is a dependency someone later assumes is load-bearing.

The honest framing for an interview: *the plan said measure it and drop it if it lost, and
it lost.* The next thing worth trying is not another OCR engine but giving the model the
fragment **coordinates** it currently never sees — it is asked to rebuild a grid from text
that has already been flattened into a single stream.

---

## Patterns across these decisions

**Measure before reaching for the expensive option.** ADR-009 and ADR-011 both rejected the
model-based approach on evidence, and ADR-009's threshold sweep is what justifies the model
where it *is* used.

**Prefer deterministic where a deterministic answer exists.** ADR-001, ADR-010, ADR-011,
ADR-012. Three of six graph nodes involve no model at all.

**Scope guardrails precisely.** ADR-008's citation rule broke a legitimate path by being too
broad; ADR-012's judge would have blocked correct output had it been allowed to gate.

**Keep the escape hatch cheap.** ADR-006's node purity and ADR-003's single SQL module both
make a reversal contained. Neither has been needed — which is the point: they made the
original decisions low-risk.

**Publish the unflattering result.** ADR-014's control row, ADR-012's judge failure, and the
cross-model finding that a small free model beat a larger one are all in the repo rather
than quietly omitted.
