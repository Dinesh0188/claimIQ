# ClaimIQ

**Catch the deduction before the insurer does.**

An agentic auditor for Indian hospital insurance claims. Upload a bill, and a
6-node LangGraph agent parses it, grounds every determination in a retrieved rule
corpus, calls a deterministic calculator for the money, checks its own work, and hands
back three numbers the billing desk actually needs:

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
.venv\Scripts\python.exe -m uvicorn claimiq.api:app --host 127.0.0.1 --port 8000
```

That starts the API on `:8000` — confirm with `curl http://127.0.0.1:8000/health`.
**It runs with no key at all** — set `AI_ENABLED=false` and every screen still works on
the deterministic engine.

The primary UI is the React/Next.js app in `frontend/`, run separately so it hot-reloads
on its own:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. Add `http://localhost:3000` to `CLAIMIQ_CORS_ORIGINS` in
`.env` if the sidebar shows a connection error. Full deploy instructions (Vercel + Render):
**[DEPLOY.md](DEPLOY.md)**.

Two other UIs ship in this repo and still work, kept for specific use cases rather than
as the primary demo: `.\start.ps1` boots the API plus a bundled static site
(`web/`, landing at `/`, product UI at `/app.html`) on one port — no `npm install`
needed, good for a one-container demo. `.\start.ps1 -Streamlit` also runs the original
Streamlit analyst UI on `:8501`, kept for side-by-side comparison; see
[DEPLOYMENT.md](DEPLOYMENT.md) for that path.

---

## Measured results

Regenerate all of these; none is hand-typed.

**Retrieval** — `python scripts/bench_retrieval.py` → [RETRIEVAL.md](RETRIEVAL.md)

| strategy | recall@1 | recall@5 |
|---|---|---|
| BM25 only | 79.5% | 88.5% |
| Dense only | 85.9% | 93.6% |
| **RRF hybrid** | 85.9% | **97.4%** |

Fusion is worth **+3.8pp at k=5** and ties dense at k=1. Since the classifier is handed five
candidates, k=5 is the operative metric — but the tie is stated rather than omitted.

**Classification** — `python scripts/bench_classify.py` → [CLASSIFICATION.md](CLASSIFICATION.md)

| strategy | accuracy | **non-payable recall** | false deduction rate | citation rate |
|---|---|---|---|---|
| `head_only` control | 51.7% | 30.5% | 3.6% | 0% |
| `keyword` table | 82.8% | 74.6% | 0.0% | 100% |
| `retrieval` | 40.2% | 11.9% | 0.0% | 100% |
| `deterministic` | 81.6% | 74.6% | 0.0% | 100% |

**Read `accuracy` against the `head_only` control, not on its own.** That control ignores
the item description entirely and guesses from the billing head — and it does well because
of how this labelled set is built: all 59 non-payable items carry `head=OTHER`, so the head
column nearly determines the payable/non-payable split by itself. A headline of "100%
accuracy" would be mostly an artifact.

**`non-payable recall` is the honest column.** It measures assigning the correct list among
four — I, II, III or IV — which the head cannot indicate at all. On that column the keyword
table and the full deterministic engine both land at **74.6%**; retrieval alone manages only
**11.9%**. *false deduction rate* is the expensive error: a real medical charge wrongly
disallowed.

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

The React frontend (`frontend/`, `http://localhost:3000`) is the primary UI:

| Screen | Route | What it shows |
|---|---|---|
| **Check a claim** | `/` | Upload or pick a sample, verdict banner, waterfall from gross bill to settlement, line-item findings with citations, missing documents, narrative + action list, PDF export |
| **Claims** | `/claims` | Searchable, paginated history of every audited claim |
| **Claim detail** | `/claims/{id}` | The persisted audit for one claim — figures, findings, document gaps |
| **Leakage Dashboard** | `/dashboard` | Portfolio leakage: preventable loss by cause, top 10 billing mistakes, most-missed documents |
| **Rule catalog** | `/rules` | All 104 corpus chunks, searchable, with the "unverified snapshot" banner |

The legacy Streamlit UI (`.\start.ps1 -Streamlit`, `:8501`) additionally has **Trace**
(each node's latency, tokens, cache hits, and the repair loop firing) and **Ask**
(plain-English questions → validated read-only SQL over the portfolio) — both still
work against the same API (`GET /api/trace/{claim_id}`, `POST /api/analytics/ask`), the
React frontend just doesn't have screens for them yet.

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

## Security and privacy

Stated plainly, because this touches medical billing:

- **No real patient data, anywhere.** Every claim, bill and corpus entry in this repo is
  synthetic and generated by `scripts/gen_samples.py`. No PHI has ever entered the system.
- **Authentication and tenancy are off by default; the audit ledger is always on.**
  The demo ships bound to `127.0.0.1` with no keys, so a clone works unconfigured.
  Setting `CLAIMIQ_API_KEYS` (`key:tenant:scope` entries, constant-time comparison)
  turns on API-key auth with per-tenant scope enforcement. The append-only ledger records
  every determination by default (`CLAIMIQ_LEDGER=true`); until keys are set it records
  `key_id="anonymous"` under tenant `"local"`. A public bind with no keys is reported
  as `insecure_deployment` on `/health`. Do not point it at real claims until the operator
  has enabled auth.
- **Secrets stay out of the repo.** `.env` and `providers.json` hold API keys and are
  gitignored; `providers.example.json` is the committed template. Verify before any push:
  `git grep -nE "gsk_|sk-or-v1-" -- . ':!*.md'` must return nothing.
- **Text-to-SQL is guardrailed structurally, not by prompt.** Single statement, `SELECT`
  only, allow-listed views, forbidden-keyword scan, executed on a `PRAGMA query_only`
  connection. The model behaving well is not the security control.
- **Uploaded PDFs are processed in a temp file and deleted** in a `finally` block; nothing
  is retained server-side.
- **The LLM cache is content-hashed on disk** in `.cache/`. If you ever point this at real
  data, that cache holds claim content and is gitignored for a reason.

## Limitations

- **The rule corpus is unverified.** 104 rules written from publicly circulated non-payable
  lists, never checked against a current IRDAI circular. Stamped `unverified-*` and shown
  in the UI and on every generated PDF.
- **Estimates, not adjudications** — three insurer profiles produce a range, not a
  prediction of any specific adjudicator.
- **Vision extraction is not production-ready.** It has never completed a full bill inside
  the free-tier quota. Native PDFs use a deterministic parser that is both cheaper and more
  accurate.
- **Benchmarks are upper bounds.** Synthetic bills have cleaner department labelling than
  real ones — see the `head_only` leak in [EVALUATION.md](EVALUATION.md).

## Tests

```bash
python -m pytest -q
```

230 tests, ~15s, no API key needed — the suite forces `AI_ENABLED=false` so it is
offline and deterministic. (The number has grown since the original 59 as the
operational layer — idempotency, retention, webhooks, ledger, batches — was added;
see [ENTERPRISE.md](ENTERPRISE.md).)

## Documentation

| | |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | components, data flow, schema, prompts, API, scalability |
| [DECISIONS.md](DECISIONS.md) | 14 decision records — problem, alternatives, trade-off, outcome |
| [EVALUATION.md](EVALUATION.md) | methodology, datasets, failure analysis, threats to validity |
| [DOCUMENTATION.md](DOCUMENTATION.md) | project overview, challenges log, 20 interview Q&As |
| [ENTERPRISE.md](ENTERPRISE.md) | the operational layer — auth, ledger, batches, retention, what's still missing |
| [DEPLOY.md](DEPLOY.md) | **current**: React frontend on Vercel + FastAPI backend on Render |
| [DEPLOYMENT.md](DEPLOYMENT.md) | legacy: single-process Streamlit deployment |
| [project-readiness/PROJECT_COMPLETION.md](project-readiness/PROJECT_COMPLETION.md) | current build/run/verification status, what's done vs. outstanding |
| generated | [RETRIEVAL.md](RETRIEVAL.md) · [CLASSIFICATION.md](CLASSIFICATION.md) · [EXTRACTION.md](EXTRACTION.md) · [EVAL.md](EVAL.md) |
