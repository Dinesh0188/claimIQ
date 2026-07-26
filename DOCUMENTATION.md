# ClaimIQ — Project Documentation

Complete write-up: the problem, the architecture, every stack choice and what it earns
its place doing, the challenges hit while building, deployment, and interview prep.

---

## 1. The problem

An Indian hospital submits a claim packet to a TPA or insurer. Weeks later money comes
back — usually less than was billed — with a deduction sheet nobody on the billing desk
fully understands. By then the patient has gone home, the bill is closed, and whatever
was disallowed is a fight or a write-off.

Three distinct people are hurt by the same information gap:

**The patient** learns their out-of-pocket number at the discharge counter instead of at
admission. The single largest cause is the *proportionate deduction*: choose a room above
the policy's room-rent cap and the insurer scales down not just the room charge but a
whole basket of associated charges — surgeon's fee, OT, nursing, anaesthesia — by the
same ratio. On the sample cardiac claim in this repo, a room ₹6,000/day over cap turns
into a **₹1.45 lakh** hit on a ₹4.1 lakh bill. Nobody told the family at admission.

**The hospital** loses money it never notices. The non-payable framework is not one list,
it is four, and they behave differently:

| List | Nature | Who absorbs it | Correct fix |
|---|---|---|---|
| **I** — Optional | Baby food, telephone, laundry, attendant meals | **Patient** | Tell them at admission, take a signed acknowledgement |
| **II** — Subsumed in room | Gowns, bed pans, hand wash, linen, ID bands | **Hospital** | Fold into the room tariff |
| **III** — Subsumed in procedure | Drapes, sterile gloves, gauze, CSSD | **Hospital** | Fold into the procedure package |
| **IV** — Subsumed in treatment | Registration, medical records, service charges | **Hospital** | Fold into treatment cost |

A List I hit means *warn the patient*. A List II/III/IV hit means *your billing master is
wrong and you are losing this on every single claim you file* — the same twelve items,
forever, across thousands of claims.

**This distinction is the product.** A naive blocklist says "these 199 items are
non-payable, delete them" and surfaces only the first finding. The second one is worth far
more and is invisible without modelling the `bearer`.

**The TPA processor** manually triages packets that are missing an MLC, an implant
invoice, or indoor case papers — queries that were predictable before submission.

### What ClaimIQ does

Audits the packet **before** it is submitted, and returns:

1. What will likely be deducted, line by line, with a citation for each.
2. What documents are missing, ranked by whether they block or merely delay.
3. What internal inconsistencies will trigger a query — dates outside the admission
   window, pre-auth variance, duplicated lines.
4. Three reconciling numbers: settlement, patient liability, hospital write-off.
5. Across a portfolio: which billing habits leak the most money.

### What it explicitly is not

- Not a claims management system. Stateless audit; it does not submit or track anything.
- Not an adjudicator. It estimates under configurable rules; a human decides.
- Not a source of legal truth. The corpus is an unverified snapshot, and says so on screen.

---

## 2. Architecture

### The agent graph

```
classify → compute → readiness → explain → verify ──┬──→ report → END
                                     ↑              │
                                     └── repair ────┘  (bounded, max 2)
```

Six nodes. Ingestion, extraction and normalisation are real stages, but they run inside
`extract_bill` before the graph starts — counting them as nodes would overstate the graph.

The backward edge out of `verify` is the reason this is a graph rather than a chain, and
it is the only reason LangGraph earns its dependency.

**→ Full component walkthrough, node contracts, data flow, persistence design, all six
prompts and the API surface: [ARCHITECTURE.md](ARCHITECTURE.md).** That document is the
detailed reference; this section is orientation only.

### The load-bearing design decision

**The LLM reasons and decides. It does not do arithmetic.**

Every rupee is computed by `claimiq/tools/waterfall.py` in `Decimal`. The model receives
finished numbers and is forbidden from producing one that was not supplied.

This is not a hedge against AI — it is what makes the guardrail possible. Because the
arithmetic is deterministic and reproducible, a disagreement between the narrative and
the tool output is **detectable**. Node 8 detects it and re-prompts with the specific
problem. If the model had produced the numbers, there would be nothing to check them
against, and "did it hallucinate?" would be unanswerable.

Invariant asserted across every sample and every insurer profile in the test suite:

```
projected_settlement + patient_liability + hospital_writeoff == gross_bill   (exactly)
```

Getting this wrong is the classic failure in this problem domain: the proportionate
deduction lands on the **patient**, not the hospital, and implementations that misattribute
it produce a write-off number that is wildly overstated and a liability number that is
wildly understated.

### Layout

```
claimiq/
  state.py          all Pydantic models + the agent state
  config.py         env settings, provider-agnostic
  llm.py            OpenAI-compatible client, schema retry, disk cache, model check
  graph.py          LangGraph wiring + audit() entry point
  trace.py          per-node instrumentation → JSONL
  store.py          SQLite persistence
  analytics.py      portfolio aggregation + guardrailed text-to-SQL
  report.py         audit PDF
  api.py            FastAPI
  nodes/            classify · compute · readiness · explain · verify · extract · report
  retrieval/        corpus parser · hybrid search (BM25 + dense + RRF)
  tools/            waterfall · consistency · documents   ← all deterministic
  eval/             LLM-as-judge
  providers.py      named provider profiles (Groq / OpenRouter / DeepSeek / OpenAI)
corpus/             the rule + policy knowledge base (markdown, hand-authored)
ui/                 Streamlit: Audit · Trace · Dashboard · Ask
scripts/            index_corpus · gen_samples · gen_bill_pdf · seed_db
                    bench_retrieval · bench_classify · bench_extract · run_eval
tests/              59 tests, offline, no key required
```

---

## 3. Stack — and what each piece actually earns

### AI / ML layer

| Component | Choice | What it contributes | Why not the alternative |
|---|---|---|---|
| Orchestration | **LangGraph** | State machine with conditional edges; the `verify → explain` cycle needs real branching and bounded looping | A chain cannot express a cycle. Nodes stay pure functions so the framework is swappable in ~30 lines |
| Reasoning LLM | **Groq `openai/gpt-oss-120b`** | Classification verdicts, coverage reasoning, narrative, text-to-SQL | Free tier, ~500 tok/s so a live demo never stalls, native JSON mode |
| Scanned bills | **RapidOCR (onnxruntime) → text LLM** | Local OCR turns a scan into ~1,500 text tokens the chat model structures | Vision is billed a flat ~11,111 tokens/image against an 8,000/min free cap — one image exceeds the whole allowance. OCR is free, offline, no torch, no system binary |
| Vision LLM | **Groq `qwen/qwen3.6-27b`** | Last-resort fallback when OCR and the text layer both fail | Unusable on the free tier (see above); the path exists and works on a paid tier |
| Provider layer | **OpenAI SDK + `providers.json` profiles** | Groq / OpenRouter / DeepSeek / OpenAI behind one client, switchable live in the UI | All speak the same wire format. A file rather than env vars because four values must change together, and `supports_json_mode` differs per provider |
| Structured output | **Pydantic + JSON mode + bounded retry** | Model output validated into typed objects; on failure it is re-prompted *with the validation error* | ~40 lines, no framework lock-in, works identically across all three providers |
| Embeddings | **fastembed · BAAI/bge-small-en-v1.5** | Dense semantic retrieval over the rule corpus | ONNX runtime, ~130 MB, **no PyTorch**. sentence-transformers would mean a 2 GB install before anyone can clone and run |
| Vector search | **numpy cosine** | Ranks 104 chunks in microseconds | A vector database at this scale is theatre. `np.dot` + argsort is 20 lines |
| Sparse retrieval | **rank_bm25** | Catches exact tokens embeddings blur — drug names, "CSSD", "MRD" | Pure Python, tiny |
| Fusion | **Reciprocal Rank Fusion** | Combines BM25 + dense without tuning a weight | Fuses *ranks*, so incomparable score scales stop being a problem. ~15 lines, no reranker model |
| Caching | **diskcache**, content-hash keyed | Repeat demos are instant and free; cache-hit rate is visible in the Trace screen | |
| Evaluation | **hand-rolled + LLM-as-judge** | Groundedness and usefulness scoring per scenario | Writing it means being able to explain every metric, which RAGAS does not buy |

### Application layer

| Component | Choice | Contribution |
|---|---|---|
| API | **FastAPI + uvicorn** | Real HTTP boundary. The UI never imports the engine, which keeps the API from being decorative |
| Models | **Pydantic v2** | One schema definition serves validation, the API contract, and LLM structured output |
| UI | **Streamlit + Plotly** | Four screens; the Plotly waterfall is the chart that makes the deduction legible |
| Money | **`Decimal`, never float** | A claim auditor off by a paisa from binary floating point is worse than no auditor. Persisted as **integer paise**, with rupee views for readers, so the guarantee survives storage |
| Documents | **pdfplumber · pypdfium2 · reportlab** | Text layer / rasterisation / generating bills *and* rendering the audit report — one dependency doing two jobs |
| Storage | **SQLite (stdlib)** | Portfolio persistence. Three tables + two rupee views, no ORM |
| Analytics | **pandas** | Aggregation for the dashboard |
| Testing | **pytest, ruff** | 59 tests, offline, ~12s |

### Deliberately rejected

| Rejected | Reason |
|---|---|
| ChromaDB | 104 chunks. Drags in onnxruntime and pydantic pins that fight the stack, to replace a dot product |
| sentence-transformers | Same embeddings as fastembed for 15× the install size |
| Cross-encoder reranker | Means torch, for an unmeasured gain over RRF |
| `simpleeval` / YAML rule DSL | Document conditions are a dozen Python predicates that read perfectly well as code. A DSL would add a parser *and* a sandbox-escape question in exchange for nothing |
| pytesseract, EasyOCR, PaddleOCR | Tesseract needs a system binary; the other two need PyTorch. RapidOCR runs on the onnxruntime fastembed already installs |
| mypy strict, pre-commit, CI matrix | Ceremony at this size |
| `instructor` | Pydantic + JSON mode + retry is 40 lines and provider-neutral — and keeps per-provider capability differences visible instead of hidden until they break |
| Postgres | 302 rows, one writer, embedded reads. See [ADR-003](DECISIONS.md) |

---

## 4. How it works, end to end

Following `data/samples/cardiac.json` — CABG, 8 days, ₹12,000/day room against a
₹6,000 cap, gross **₹4,10,000**.

*Structural reference — node contracts, models, schema, prompts — is in
[ARCHITECTURE.md](ARCHITECTURE.md). This section is the same system told as one claim.*

**Ingest & extract.** Cheapest path that works, in order. A native PDF's ruled table is
parsed directly by `pdfplumber` — no model, and measured at **100% row recall with exact
bill totals**. A scan with no text layer goes to local RapidOCR, whose text the ordinary
chat model structures. The vision model is the last resort, and on Groq's free tier it is
effectively unavailable: a flat ~11,111 tokens per image against an 8,000/minute cap.
Rows the extractor is unsure of carry confidence < 0.7 and are flagged amber, not silently
trusted.

**Classify.** Each line runs hybrid retrieval. `"STRL GLV 7.5"` → BM25 hits it on the
alias token, dense hits it semantically, RRF ranks `L3-002` first. The LLM sees the item
plus five candidates and returns a verdict that **must** name the chunk it decided from.
An uncited non-payable verdict is rejected and downgraded to `UNMAPPED` — never trusted.

Result: 4 List I items (patient, ₹4,250), 9 hospital-borne items across Lists II/III/IV
(₹7,750), the rest payable.

**Compute.** The agent calls the waterfall tool. Six steps, order configurable:

```
gross 410,000
  − item deductions (Lists I–IV)         → 399,370 payable subtotal
  − room rent excess   (12,000−6,000)×8  → 48,000
  − proportionate      assoc × (1 − ½)   → 97,000
  − procedure sub-limit                  → n/a
  − deductible                           → n/a
  − co-pay 10%                           → 25,300
  − balance sum insured cap              → n/a
  = 227,700 estimated settlement
```

Run three times, once per insurer profile, because **which heads count as "associated"
varies by insurer**. Pharmacy, implants and diagnostics usually escape; surgeon, OT and
nursing usually do not; consultations are contested. Output is a **range**
(₹2,10,600 – ₹2,31,300), never one confident number.

Buckets: settlement ₹2,27,700 + patient ₹1,74,550 + hospital ₹7,750 = ₹4,10,000 exactly.

The agent then calls `simulate_room_downgrade` unprompted: inside the cap, settlement
rises ~₹88,000. *One conversation at admission is worth that to the family.* That is the
demo moment.

**Readiness.** Document requirements are conditional predicates over the claim — accident
→ MLC, implant → implant invoice with batch sticker, reimbursement or >₹1L → indoor case
papers. Nine consistency checks run: room days vs length of stay, service dates outside the
admission window, pre-auth variance, duplicate lines, unmapped share, round-number density.

**Explain → Verify → Repair.** The model writes the narrative from the computed figures.
The verifier then checks three things — the invariant, citation coverage on model output,
and whether every rupee figure in the prose corresponds to one the engine actually
produced. On a real run during development the first narrative referenced a figure the
engine never computed; the verifier caught it, re-prompted with that specific complaint,
and the second pass reconciled. The Trace screen shows `explain → verify → explain →
verify → report`.

**Report & persist.** Audit PDF stamped with the corpus version; a row into SQLite, with
money stored as integer paise so the `Decimal` guarantee survives the storage boundary.

**Portfolio.** Across 300 seeded claims the dashboard aggregates preventable hospital loss
by cause, the top 10 recurring billing mistakes, and the most-missed documents. The Ask
screen turns "which billing mistake cost us most?" into validated read-only SQL.

---

## 5. Challenges faced

Every one of these changed the design.

**1. Cosine similarity cannot separate payable from non-payable.**
The obvious build is: embed the catalog, embed the bill line, threshold the similarity.
Measured, it fails. "OT charges" (payable) scores **0.756** against the catalog while
"STRL GLV 7.5" (non-payable) scores **0.591** — the distributions overlap. Sweeping the
threshold trades one error for the other and never escapes:

| threshold | non-payable recall | real charges wrongly disallowed |
|---|---|---|
| 0.65 | 79.7% | **50.0%** |
| 0.70 | 69.5% | 28.6% |
| 0.82 | 62.7% | 0.0% |

*Resolution:* the threshold sits at 0.82 where the costly error is zero, and the LLM
recovers the recall — 96.6% non-payable recall against 62.7% for the best non-LLM strategy. This is
the measured justification for the AI, rather than an assumption.

**2. The retrieval-only classifier was worse than a keyword table.**
40.2% versus 73.6%. Uncomfortable, and worth reporting rather than hiding. The eventual
no-key fallback is keyword-first with retrieval on the residual: 74.7%, beating both halves.

**3. The model confused primary services with same-named ancillary charges.**
Early runs classified "Room charges - Single AC" as the *air-conditioning surcharge*
(List II) and "OT charges" as the *OT booking fee* (List III) — both real medical charges
wrongly disallowed. *Resolution:* the `head` field is decisive information that was being
ignored. The prompt now states that ROOM/PROCEDURE/PHARMACY heads mark the primary billed
service and only `head=OTHER` lines are LIST_* candidates, with the two confusions named
explicitly.

**4. Requiring citations broke deterministic mode.**
The verifier demanded a chunk citation on every non-payable finding. Keyword-table matches
have no chunk to cite, so with AI off *every* audit failed verification. *Resolution:* add
`source` to each finding and enforce citations only on `llm` output. The point of the rule
is to stop the model asserting things it cannot point at; a hand-written table is already
auditable source code. Caught by a test, not in production.

**5. Trace showed zero tokens.**
`try_client()` minted a fresh client per call, so the tracer read an empty call list.
*Resolution:* cache the client — the trace attributes usage by reading `client.calls`,
which only works if every caller shares one instance.

**6. Groq deprecated two models mid-project.**
`llama-3.3-70b-versatile` and `llama-3.1-8b-instant` were retired on 17 June 2026. A
hardcoded model ID would 404 in front of an interviewer. *Resolution:* the client queries
`GET /models` at boot and, if the configured ID is absent, logs the names that *are*
available. Surfaced in `/health` and the sidebar.

**7. Seeding 300 claims through the agent hits rate limits.**
~8 LLM calls per claim × 300 is not survivable on a free tier. *Resolution:* 20 claims
through the full agent, the rest through the deterministic engine, **and the dashboard
states the split on screen** rather than implying the model saw everything.

**8. Tests took 125 seconds because they called the LLM.**
*Resolution:* `conftest.py` forces `AI_ENABLED=false`. The suite is now 12s, offline, and
runnable by anyone who clones the repo without a key. LLM quality is measured separately
by the benchmark scripts, where non-determinism is the subject rather than a nuisance.

**9. The three-bucket invariant is easy to break.**
Quantising each step independently makes the numbers drift; attributing the proportionate
deduction to the hospital inverts the headline. *Resolution:* deductions are quantised as
they are taken, settlement is derived by subtraction rather than computed independently,
and the invariant is a parametrised test over every sample × every profile.

**10. Vision extraction looked broken, and the cause was a setting I never set.**
The vision model returned 5–22% of rows on multi-row bills and, on the 37-row bill, an
empty generation with `json_validate_failed`. The obvious readings were "the model can't
count rows" or "the image is too dense", and I acted on the second — slicing pages into
horizontal bands to shorten each generation. That made it **worse**: recall fell from 22%
to 10%, because cropping removes the table context that tells the model what a column means.

Reading the error body properly gave it away: *"max completion tokens reached before
generating a valid document."* The output was being truncated mid-JSON by a provider
default I had never overridden. Setting `max_tokens=8000` took vision recall from
**5.4% to 81.1%** with 100% amount accuracy on the rows returned.

The lesson worth keeping: I spent two iterations tuning the prompt and the images for what
was a one-line configuration bug, because I pattern-matched to "model limitation" instead
of reading what the API actually said.

**Caveat, and do not quote the 81.1% as reproducible.** It was measured on a single bill in
a window where the per-minute token budget happened to allow it. On the current free tier it
cannot be reproduced at all — see challenge 14. If asked about vision extraction, the honest
answer is "the code path works and I measured 81% once on one bill, then hit a quota ceiling
that makes it unavailable on the free tier; the deterministic path covers native PDFs at
100% and that is what the demo uses."

Separately, native PDFs do not need the model at all — pdfplumber parses their ruled
tables at **100% row recall with exact bill totals**. So extraction now takes the cheap
deterministic path when the document supports it and reserves the model for scans. Same
judgement as keeping arithmetic out of the model: use it where it is irreducible.

**11. A 100% benchmark score that was mostly my own dataset leaking.**
After fixing the prompt to treat the billing `head` as decisive, the grounded classifier
scored 100% accuracy, 100% non-payable recall, 0% false deductions. A perfect score on a
messy real-world task is a reason for suspicion, not celebration, so I went looking for
the leak — and found it in my own labelled set.

All 59 non-payable items in it carry `head=OTHER`, and 27 of the 28 payable ones do not.
A rule of "head is not OTHER → payable", knowing nothing whatsoever about the item, splits
payable from non-payable at **98.9%**. By instructing the model that the head is decisive,
I had handed it the answer to the binary question and then congratulated it for knowing.

*Resolution:* a `head_only` control strategy is now a permanent row in the benchmark. It
ignores the description entirely and scores 51.7% accuracy — which immediately tells any
reader how much of everyone else's accuracy is free. And the reported headline moved to
**non-payable recall**, which measures assigning the correct list among four and which the
head cannot indicate at all. On that column the control gets 30.5%, the best deterministic
strategy 62.7%, and the LLM 96.6% on `gpt-oss-120b` or 100% on `gemma-4-26b`. That gap is
real; the accuracy column mostly was not.

A second result fell out of re-running this across providers, and it is worth mentioning
unprompted in an interview: **the small free Gemma 4 26B beat the much larger
`gpt-oss-120b`** — 100%/100%/0% against 96.6%/96.6%/3.6%. On a narrow task where retrieval
already supplies the candidates, the extra capacity bought nothing and cost false
deductions. Both rows are published in `CLASSIFICATION.md` rather than quietly reporting
the better one.

Worth stating plainly: the head *is* legitimate signal — real bills do carry a department
column. The flaw is that in synthetic data I assigned those heads myself, so they separate
far more cleanly than they would on a real hospital bill where consumables get filed under
whatever the billing clerk picked.

**12. The verifier cried wolf on half the scenarios.**
The first full evaluation run showed the verifier failing 2 of 4 canonical scenarios after
exhausting all three repairs — including `clean`, a claim with no deductions at all. A
guardrail that rejects valid output half the time is worse than no guardrail, because the
team learns to ignore it.

Both failures were false positives, and they had different causes:

- On `clean`, the narrative quoted our own advisory text back at us — "exact multiples of
  **1,000**". That 1,000 is a threshold the engine supplied in a consistency-flag message,
  not a money figure, but the money regex could not tell the difference.
- On `incomplete`, the narrative stated the pre-authorisation variance, ₹65,800 — which is
  ₹165,800 − ₹100,000. The engine had emitted both operands but never the subtraction, so a
  legitimately derived figure looked invented.

*Resolution, and the interesting part:* the two fixes are deliberately different in kind.
For the first, numbers appearing in our own flag messages, document-gap reasons and
deduction bases are now folded into the grounded set — they genuinely are supplied facts.
For the second, the tempting fix was to let the verifier accept arithmetic on known figures,
but that would have gutted it: with ~30 known values there are ~900 pairwise differences,
and almost any invented number would find a match. So instead the *input* was fixed — the
variance is now computed by the engine and handed to the writer as a fact. The model quotes
a supplied number, and the verifier stays strict.

That is the general principle worth stating in an interview: **when a guardrail produces a
false positive, first ask whether the upstream input was incomplete.** Loosening the check
is the easy fix and usually the wrong one. Both scenarios now pass with zero repairs, and
both cases are pinned by regression tests in `tests/test_verify.py`.

**13. The LLM judge scored 1.2/5 on output that was actually correct.**
After fixing the verifier, the evaluation run came back with mean groundedness of **1.2/5** —
the judge claiming the narratives were riddled with unsupported figures. The deterministic
verifier had passed every one of those same narratives.

One of them had to be wrong. The judge's own list of "unsupported" claims settled it:

- *"loss of INR 24,300"* — the hospital write-off, straight off the waterfall
- *"Single AC at INR 12,000/day against a policy cap of INR 6,000/day"* — both supplied
- *"exceeds the pre-authorised amount by INR 60,000"* — supplied since fix #12

Every one was grounded. The bug was in my harness: `judge_explanation` rebuilt its own
compact summary of the facts instead of using the fact set the writer actually received.
That summary silently dropped the room rate, the pre-authorisation figures and the
consistency-flag text. The judge was marking statements unsupported because *it* could not
see the evidence — not because the writer had invented anything.

*Resolution:* the exact fact set is now persisted on the state as `facts_json` and handed
to the judge verbatim, and the judge's rubric was told that a figure appearing anywhere in
the facts — including inside a flag string — counts as supported. A separate fix: a judge
call that fails now records `None` rather than `0`, because scoring an unreachable judge as
the worst possible grade silently defames the output and poisons the mean.

Two things worth taking from this. First, **judging against less evidence than the writer
had measures your harness, not your model** — and a bad eval is more dangerous than no eval,
because it looks like data. Second, the deterministic verifier and the LLM judge disagreed,
and the deterministic one was right. That is the entire argument for keeping the checks that
gate output mechanical and leaving the model to score things that are genuinely subjective.

**14. Vision extraction is impossible on Groq's free tier — and it does not matter much.**
Scanned-bill extraction failed with a 413 on every page: `qwen/qwen3.6-27b` is billed a flat
**11,111 tokens per image** while the free tier caps a request at **8,000 tokens per minute**.
One image costs more than the entire per-minute allowance.

The instinct is to shrink the image. I did — dropped the render scale from 2.0 to 1.4, a
little over half the pixels — and Groq reported *exactly 11,111 tokens again*. The billing is
flat per image, independent of resolution, so there is no downscaling that gets under the
bar. Page-slicing was tried earlier and separately made recall worse (22% → 10%), because
cropping destroys the table context the model needs to interpret a column.

*Resolution:* stop fighting it. Extraction already prefers a deterministic `pdfplumber`
table parse and only reaches for the model when a page has no text layer at all — and that
deterministic path measures **100% row recall, 100% amount accuracy, exact bill totals** on
native PDFs. Vision is the fallback for true scans, and it works on a paid tier or any
provider that does not price images this way; the code path is unchanged either way.

The general lesson is the one this project keeps relearning: when a model-based step hits a
wall, check whether a deterministic path can do the job better before optimising the model
call. Here the deterministic path was not just cheaper, it was *more accurate* — 100% versus
the 81% the vision model managed on the one bill that fit inside the quota.

**15. Round-number check fires on our own synthetic data.**
The generator emits round figures, so the weak fraud signal trips on ~91% of sample bills.
Left visible and documented as a synthetic-data artifact rather than tuned away to make a
demo look clean.

---

## 6. Deployment

Design is deploy-ready: Chroma was avoided, embeddings cache to a file keyed by corpus
hash, SQLite is seedable at build time.

### Recommended — Streamlit Community Cloud (free)

Best effort-to-payoff for a portfolio link.

1. Push to GitHub. **Confirm `.env` is gitignored** — it is, but check before the first push.
2. Commit the seeded `data/claimiq.db` and `.cache/embeddings/*.npy` so the deployed app
   has a populated dashboard and does not re-embed on cold start.
3. On [share.streamlit.io](https://share.streamlit.io), point at `ui/app.py`.
4. Add `LLM_API_KEY` under **App settings → Secrets**, never in the repo.
5. Run API and UI in one process for a single free dyno — either start uvicorn in a
   background thread on Streamlit boot, or set `API` in `ui/_shared.py` to a separately
   hosted API URL.

**Free-tier caveat:** the app sleeps after inactivity and takes ~30s to wake. For a
recruiter clicking a link cold, consider leading with a recorded walkthrough and offering
the live link second.

### Alternative — Render / Railway / Fly.io

Better if you want the FastAPI boundary genuinely visible (a real `/docs` page is a good
artifact). Two services from one repo:

```
web:  uvicorn claimiq.api:app --host 0.0.0.0 --port $PORT
ui:   streamlit run ui/app.py --server.port $PORT --server.address 0.0.0.0
```

Set `LLM_API_KEY` as an environment variable and point the UI's `API` constant at the
deployed API URL. Free tiers also cold-start.

### Alternative — Hugging Face Spaces

Streamlit SDK, generous free tier, and an AI-adjacent audience. Same single-process
caveat; secrets via the Spaces UI.

### Docker (portable, and a good README artifact)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python scripts/index_corpus.py
EXPOSE 8000 8501
CMD uvicorn claimiq.api:app --host 0.0.0.0 --port 8000 & \
    streamlit run ui/app.py --server.port 8501 --server.address 0.0.0.0
```

### Before you deploy anything

- `.env` is gitignored; the key goes in the host's secret manager.
- Keep the "unverified snapshot / synthetic data" banners. They are the difference between
  a careful project and a liability.
- Do not put real patient data in a public deployment. There is no auth, and none was
  scoped.

---

## 7. Twenty interview questions and answers

### Architecture and AI engineering

**1. Walk me through the architecture.**
A six-node LangGraph agent — `classify → compute → readiness → explain → verify → report`,
with a bounded `verify → explain` repair cycle. Ingestion, extraction and normalisation are
real stages but they run inside `extract_bill` before the graph starts, so they are not
nodes; describing them as such would overstate the graph. Hybrid
retrieval over a rule corpus grounds each classification; an LLM issues verdicts that must
cite the chunk they came from; a deterministic tool computes the money; a verifier node
reconciles the model's narrative against the tool output and drives a bounded repair loop;
then explanation, report, persistence. State is a Pydantic model flowing through pure
functions — nothing in `nodes/` imports LangGraph, so the framework is swappable.

**2. Why does the LLM not do the arithmetic? Isn't that a cop-out for an AI project?**
The opposite — it is what makes the guardrail possible. Because the arithmetic is
deterministic and reproducible, disagreement between the narrative and the tool is
*detectable*, so the verifier detects it and re-prompts. If the model produced the numbers,
"did it hallucinate?" would be unanswerable. The model has judgment; the tool has
precision. It is the same reason a coding agent gets a Python interpreter instead of being
asked to do long division.

**3. Why is there an LLM at all? Couldn't fuzzy matching do this?**
I measured it. Cosine similarity does not separate payable from non-payable — "OT charges"
scores 0.756 against the catalog and "STRL GLV 7.5" scores 0.591. At a threshold with
useful recall (0.65) you wrongly disallow 50% of real medical charges. The best non-LLM
strategy reaches 74.7% accuracy and 62.7% non-payable recall; the grounded LLM reaches
96.6% on both. The LLM is in the design because a number said it had to be, not because
the project needed AI in it.

Quote the *non-payable recall* column, not accuracy — accuracy on this set is inflated by a
`head` field that nearly gives away the payable/non-payable split on its own (see challenge
11). Recall measures assigning the correct list among four, which the head cannot indicate.

**4. Why LangGraph and not a plain loop?**
Honestly, for a linear pipeline I would not have. The justification is the cycle: `verify`
routes backward to `explain` conditionally, bounded at two repairs. That is what LangGraph
is for, and I get state management and streaming for the Trace screen. I hedged by keeping
every node a pure function, so if it became a liability the port is ~30 lines.

**5. Explain your retrieval setup and prove it was worth it.**
BM25 for exact tokens, BGE-small dense embeddings for paraphrase, fused with Reciprocal
Rank Fusion. RRF fuses *ranks*, so I never have to tune a weight between incomparable score
scales. Measured on 78 hand-labelled queries: BM25 88.5% recall@5, dense 93.6%, RRF 97.4%.
I set the rule up front that if fusion had not beaten both, I would ship the simpler one.

**6. How do you prevent hallucination?**
Three layers. Structurally, every non-payable verdict must name a real chunk ID; an uncited
one is rejected and downgraded to UNMAPPED rather than trusted. Numerically, the verifier
extracts every rupee figure from the narrative and checks it against the set the engine
actually produced. Procedurally, a failure triggers a *targeted* re-prompt naming the
specific problem, not a blind retry — and it is bounded at two so a model that cannot be
talked into consistency degrades instead of looping.

**7. Why no vector database?**
104 chunks. A dot product against a 104×384 numpy array is microseconds. ChromaDB would
add onnxruntime and pydantic pins that fight the rest of the stack to replace twenty lines.
If the corpus reached six figures I would revisit — the retrieval interface is one class,
so swapping the backend is contained.

**8. How do you handle structured output across providers?**
Pydantic schema in the system prompt, JSON mode on, validate the response, and on a
`ValidationError` re-prompt with the actual error text appended, bounded at two retries.
About forty lines. I avoided `instructor` so the same code works unchanged against Groq,
DeepSeek, OpenRouter and OpenAI.

Providers are named profiles in `providers.json`, not loose environment variables, because
switching means changing four related values together — base URL, key, chat model, vision
model — plus a capability flag. Four env vars that must move in lockstep is a footgun; one
named profile is not, and the UI can then offer a live switch.

The capability flag earns its place: not every OpenAI-compatible endpoint implements
`response_format`. Free community models often do not, and sending it gets the whole request
rejected — so `supports_json_mode` is declared per provider, and when it is off the client
falls back to prompt-plus-validation and strips markdown fences before parsing.

This stopped being theoretical when Groq's quota ran out mid-session and again when Groq
started returning 403 for the whole network: failing over to OpenRouter was a dropdown, not
a code change.

**9. What did you actually measure?**
Retrieval recall@1/3/5 per strategy; classification accuracy, non-payable recall, false
deduction rate and citation rate per strategy; vision extraction row recall and amount
accuracy against generated ground truth; and per-scenario groundedness/usefulness from an
LLM judge plus latency and tokens. Every figure in the README is written by a script — none
is typed by hand.

**10. Your LLM judge shares a model family with the thing it judges. Isn't that circular?**
Yes, and I say so in EVAL.md. Judge scores are directional — good for catching a regression
between prompt versions, not for claiming an absolute quality level. They gate nothing. The
things that actually block output are deterministic: the arithmetic invariant and citation
coverage.

I have a concrete reason to distrust it beyond the theory. The judge once scored my output
**1.2/5 for groundedness** on narratives the deterministic verifier had already passed. One
of them had to be wrong, and it was the judge — it was flagging figures like the hospital
write-off as invented. The cause was mine: `judge_explanation` rebuilt its own compact
summary of the facts instead of using the set the writer received, silently dropping the
room rate, the pre-authorisation figures and the flag text. It was marking statements
unsupported because *it* could not see the evidence.

Fixing the harness to pass the identical fact set moved groundedness from 2.7 to 4.5/5 with
no change to the model or the prompt. Two things follow. **Judging against less evidence
than the writer had measures your harness, not your model** — and a broken eval is more
dangerous than no eval, because it looks like data. And when the deterministic check and the
LLM judge disagreed, the deterministic one was right. That is the entire argument for
keeping the gates mechanical.

### Domain

**11. What is the non-obvious insight here?**
That the non-payable framework is four lists with four different *bearers*, not one
blocklist. List I is the patient's cost — warn them at admission. Lists II/III/IV are a
hospital billing error absorbed by the hospital, repeating on every claim it files. A
blocklist surfaces only the first. Modelling `bearer` is what turns a checker into
something a CFO cares about.

**12. What is the proportionate deduction and why does it matter so much?**
If the room tariff exceeds the policy cap, the insurer scales down not just the room charge
but all *associated* charges — surgeon, OT, nursing, anaesthesia — by the same ratio. On my
sample cardiac claim that is ~₹1.45 lakh on a ₹4.1 lakh bill. It is the least understood
rule in Indian health insurance and the most financially violent, and it is entirely
avoidable with one conversation at admission. That is what the room-downgrade simulator
quantifies.

**13. How do you handle the fact that insurers behave differently?**
I do not pretend to know. Which heads count as "associated" varies, so the engine ships
three profiles — conservative, typical, lenient — and the UI reports a **range**, not a
point estimate. Fake precision was something I deliberately engineered out; the corpus also
carries `verified_on: null` and the UI renders an "unverified snapshot" banner while it
stays null.

**14. Where would this break on real hospital bills?**
Line-item descriptions I have never seen. Every benchmark here is synthetic and
hand-labelled, so the numbers measure generalisation over invented messiness, not real
bills. That is exactly why the system reports an unmapped count and per-item confidence
instead of silently assuming anything is payable. First step with real data would be
relabelling the benchmark from actual bills.

**15. Is this legally safe?**
It is framed as an estimate throughout — "estimates likely deductions under configurable
insurer rules", never "predicts the adjudicator". The corpus is marked unverified against
current IRDAI circulars. Output is a pre-submission checklist for a human, never an
automated decision. And no real patient data touches it; everything is synthetic and
watermarked.

### Engineering

**16. Why `Decimal` and not float?**
Binary floating point cannot represent 0.1 exactly, and this system adds and multiplies
money across six waterfall steps three times per claim. A rounding drift of a paisa breaks
the invariant that the three buckets sum to the gross bill — and that invariant is the main
correctness check, so I cannot afford it to be approximately true.

**17. Tell me about a bug you found and how.**
Requiring a chunk citation on every non-payable finding meant that with AI switched off —
where classification comes from a keyword table with no chunk to cite — *every* audit failed
verification. A test caught it. The fix was to add a `source` field and enforce citations
only on model output: the point of the rule is to stop the model asserting what it cannot
point at, and a hand-written table is already auditable code.

**18. How is this tested?**
59 tests, 12 seconds, no API key. `conftest.py` forces `AI_ENABLED=false` so the suite is
offline and deterministic — anyone can clone and run it. Coverage includes the three-bucket
invariant parametrised over every sample × every profile, corpus integrity (unique IDs,
bearer matches list), verifier behaviour with a *deliberately injected* inconsistent
narrative, and bounded-repair behaviour. LLM quality lives in the benchmark scripts instead,
where non-determinism is the subject rather than a nuisance.

**19. Your text-to-SQL runs model output against a database. How is that not a disaster?**
The model's good behaviour is not the security control. Generated SQL is validated before
execution: single statement only, must start with SELECT or WITH, a regex rejects
INSERT/UPDATE/DELETE/DROP/ATTACH/PRAGMA, table references are checked against an allowlist,
and it runs on a connection with `PRAGMA query_only = ON`. Failing any check raises before
anything touches the database.

**20. What would you build next?**
Three things. First, relabel the benchmarks against real anonymised bills — every number
here is synthetic and that is the biggest gap. Second, a feedback loop: capture the TPA's
actual deduction sheet after settlement and score the prediction against it, which turns
the whole thing from an estimator into something that can learn a specific insurer's
behaviour. Third, per-insurer profile learning — right now the three profiles are
hand-authored; with enough settled claims you could fit the associated-charge composition
per insurer from data instead of guessing it.

---

## 8. A disagreement between the two guardrails, left unresolved

On the cardiac scenario the deterministic verifier **passed** the narrative while the LLM
judge scored its groundedness **2/5**. Both are working correctly; they measure different
things.

The verifier asks a narrow, mechanical question — does every rupee figure in the prose
appear in the set of figures the engine computed? The judge asks a broader semantic one —
is every *claim*, numeric or not, supported by the facts? A narrative can use only real
numbers and still over-reach in how it characterises them.

That gap is not closed, and it should not be closed by loosening the judge. It is the
argument for having both: the mechanical check is trustworthy enough to gate output, the
semantic one is suggestive enough to catch what the mechanical check cannot see. Notably,
cardiac is also the scenario that triggered a repair — the narrative there was the hardest
one to get right, and both instruments independently noticed.

---

## 9. Ninety-second demo script

1. Open **Audit**, load the cardiac sample. "Gross ₹4,10,000. The family expects full
   cashless settlement." *(15s)*
2. Run it. Waterfall drops to ~₹2,27,700. "₹1,45,000 of that is one decision — a room
   ₹6,000 over cap. Nobody told them at admission." *(20s)*
3. Point at the room-downgrade banner. "Inside the cap, settlement rises ~₹88,000. One
   conversation is worth that to this family." *(15s)*
4. Point at **Hospital write-off**. "Separately, ₹7,750 of List II and III items the
   hospital billed and will never be paid for — that leaks on every claim they file." *(15s)*
5. Open **Trace**. "Ten nodes. Here the verifier caught the model's narrative disagreeing
   with the calculator and re-prompted — `explain` ran twice." *(15s)*
6. Open **Dashboard**. "Across 300 claims, here are the top ten billing mistakes by
   aggregate loss. Fix the top three in the billing master and the leak stops." *(20s)*

Lead with step 3. It is the moment the problem becomes real.
