# ClaimIQ — Architecture Deep Dive

This document explains *how the system is built and why it is built that way*. It assumes
you have read the README's overview and covers the engineering underneath it: what each
node consumes and produces, how data is shaped and stored, what every prompt is for, where
the system breaks under load, and which alternatives were rejected.

Decision records live in [DECISIONS.md](DECISIONS.md). Measurement methodology lives in
[EVALUATION.md](EVALUATION.md).

---

## 1. The shape of the system

Three layers, and the boundaries between them are the design:

```
┌──────────────────────────────────────────────────────────────────┐
│  ui/  Streamlit — 4 views, talks HTTP only                       │
├──────────────────────────────────────────────────────────────────┤
│  claimiq/api.py  FastAPI — 18 endpoints, the contract            │
├──────────────────────────────────────────────────────────────────┤
│  claimiq/graph.py  LangGraph agent                               │
│    nodes/      pure ClaimState -> dict functions                 │
│    tools/      deterministic Decimal math                        │
│    retrieval/  BM25 + dense + RRF over corpus/                   │
│    llm.py      provider-agnostic client                          │
│    store.py    SQLite persistence                                │
└──────────────────────────────────────────────────────────────────┘
```

Two rules hold this together, and everything else follows from them.

**The UI never imports the engine.** `ui/` speaks HTTP to `api.py` and nothing else. This
costs a serialisation round trip on every interaction and buys a genuine contract: the API
cannot quietly grow a dependency on Streamlit session state, and anything the UI can do,
`curl` can do. On single-port hosts `ui/_boot.py` starts uvicorn on a daemon thread rather
than collapsing the boundary — co-located processes, still HTTP.

**Nothing in `nodes/` imports LangGraph.** Every node is `ClaimState -> dict`. The framework
appears in exactly one file, `graph.py`, where it wires nodes together. Replacing LangGraph
with a hand-rolled loop is a ~30 line change touching no node.

---

## 2. The agent graph

```
classify → compute → readiness → explain → verify ──┬──→ report → END
                                     ↑              │
                                     └── repair ────┘  (bounded, max 2)
```

The backward edge from `verify` to `explain` is the reason this is a graph and not a chain.
A linear pipeline cannot express "check the output, and if it disagrees with the tool, tell
the model precisely how and try again, but give up after two attempts."

### Node contracts

| Node | Consumes | Produces | LLM? |
|---|---|---|---|
| `classify` | `packet.line_items` | `findings`, `ai_used` | yes, batched 12/call |
| `compute` | `packet`, `findings` | `profiles` (3 × `WaterfallResult`) | **no — tool** |
| `readiness` | `packet`, `findings` | `document_gaps`, `consistency_flags` | **no — pure Python** |
| `explain` | everything above | `narrative`, `action_list`, `facts_json` | yes |
| `verify` | `profiles`, `findings`, `narrative` | `verify_passed`, `verify_problems`, `repair_count` | **no — deterministic** |
| `report` | — | — | no |

Three of six nodes involve no model at all. That is deliberate: the model is used where
judgement is irreducible and avoided everywhere a deterministic answer exists.

### Why `report` is an empty node

It does nothing and returns `{}`. It exists because `verify`'s conditional edge needs a
terminal target that is not `END` — LangGraph conditional edges route to named nodes.
Persistence happens in `graph.audit()`, which owns the assembled `AuditResult`. An empty
node is cheaper than restructuring the graph to route conditionally to `END`.

### The repair loop in detail

`verify_node` returns `repair_count = state.repair_count + (1 if problems else 0)`.
`needs_repair` reads that and routes:

```python
if state.verify_passed or state.repair_count > MAX_REPAIRS:
    return "report"
return "explain"
```

On re-entry, `explain_node` sees `state.verify_problems` populated and appends them to the
prompt — a *targeted* re-prompt naming the specific failure, not a blind retry of an
identical request. The cache key includes the user message, so a repair is never served the
cached original.

Bounding matters. A model that cannot be argued into consistency degrades to "best effort
after 2 attempts, verifier flag raised" rather than looping until the request times out.

---

## 3. Data flow — the models

28 Pydantic models. They fall into four groups.

### Input: what a claim *is*

`ClaimPacket` is the root, composed of `ClaimContext` (dates, diagnosis, documents
attached), `PolicyTerms` (caps, co-pay, sub-limits), `RoomStay`, and `list[BillLineItem]`.

One decision worth explaining: **`RoomStay` is descriptive, not additive.** Room charges
live in `line_items` like every other charge; `RoomStay` only describes the room so the cap
logic has something to compare against. If room charges were additive there would be two
sources of truth for the same rupees and the gross bill could be double-counted. Instead:

```python
@property
def gross_bill(self) -> Decimal:
    return sum((li.amount for li in self.line_items), Decimal("0"))
```

One definition, one source. A consistency check (`CHK-ROOM-TOTAL`) then verifies that the
room line items reconcile with `rate_per_day × days`, catching the disagreement rather than
silently preferring one.

### Agent state: what accumulates

`ClaimState` carries the packet plus every field nodes populate. Nodes return **partial
dicts** and LangGraph merges them, so a node declares only what it changed. `ClaimState`
accumulates rather than replaces because later nodes need earlier output — `verify` needs
both `profiles` (from `compute`) and `narrative` (from `explain`), which are three nodes
apart.

`facts_json` deserves note. It stores the exact fact set `explain` was given. It exists
because the evaluation harness was scoring the judge against a *reconstructed, smaller*
summary, which made it flag grounded statements as hallucinations. Persisting the real
evidence fixed groundedness from 2.7 to 4.5/5 with no change to model or prompt. Full story
in [EVALUATION.md](EVALUATION.md).

### Output: what the API returns

`AuditResult` is the wire format. `WaterfallResult` appears three times inside it, once per
insurer profile, which is how the settlement *range* is represented rather than a single
fake-precise number.

`ItemFinding` carries the fields that make a deduction defensible:

```python
classification: Classification   # PAYABLE | LIST_I..IV | UNMAPPED
bearer: Bearer                   # who absorbs it — the whole point of the project
cited_chunk_id: str | None       # which corpus chunk justified it
source: Literal["keyword", "retrieval", "llm"]
confidence: float
```

`source` exists because the citation requirement applies **only to model output**. A
keyword-table match has no chunk to cite and needs none — the table is auditable source
code. Without `source`, requiring citations universally broke deterministic mode entirely,
since every keyword deduction failed verification.

### Structured-output schemas

`VerdictBatch`, `Explanation`, `ExtractedBill`, `SQLAnswer`, `Judgement` are never persisted
or returned — they exist purely as the contract the LLM must satisfy. Each is passed to
`client.structured(schema=...)`, which puts `model_json_schema()` in the system prompt and
validates the reply against it.

### Validation boundaries

| Boundary | Mechanism | On failure |
|---|---|---|
| HTTP → engine | FastAPI + Pydantic | 422 with Pydantic's own error body |
| LLM → engine | `schema.model_validate_json` | re-prompt with the error, bounded at 2 |
| Engine → SQLite | `_paise()` conversion | — |
| NL → SQL | AST-ish validation, allow-list | `ValueError`, 422 |

Money is `Decimal` from parse to persistence. Serialising to JSON emits strings, not floats,
so a round trip through the API is lossless.

---

## 4. Retrieval

104 chunks from hand-authored markdown in `corpus/`. Chunks are delimited by an HTML comment
carrying the ID and metadata:

```markdown
<!-- chunk_id: L2-001 | list: LIST_II_ROOM | bearer: HOSPITAL -->
### Patient gown
Aliases: gown, patient gown, hospital gown, disposable gown, OT gown
Provided as part of occupying the bed. Already inside the room tariff.
```

`chunk_id` is what every model determination must cite, so it has to be stable and
human-checkable — which is why the corpus is hand-written markdown rather than generated.

### Why three strategies fused

`search_text` repeats aliases deliberately: a hospital bill prints the alias
(`STRL GLV 7.5`), not the canonical name. Aliases carry most of the retrieval signal.

- **BM25** catches exact tokens — drug names, `CSSD`, `MRD` — that embeddings blur
- **Dense** (BGE-small via fastembed) catches paraphrase: `hand rub` → `hand wash`
- **RRF** fuses *ranks*, not scores, so no weight needs tuning between incomparable scales

```python
fused[idx] += 1.0 / (RRF_K + rank)   # RRF_K = 60
```

Measured on 78 hand-labelled queries: BM25 88.5% recall@5, dense 93.6%, **RRF 97.4%**. The
fusion is kept because a number justified it; had it lost, the plan was to keep the simpler
strategy. Note it only ties dense at k=1 — fusion improves the candidate *set*, which is
what the classifier consumes, not the single best guess.

### Why no vector database

104 chunks × 384 dimensions is a 160 KB numpy array. `self._vectors @ vector` is a single
matrix multiply — microseconds. ChromaDB would add onnxruntime and pydantic pins that fight
the rest of the stack, plus a persistence lifecycle, to replace about twenty lines. The
interface is one class, so swapping the backend is contained if the corpus reaches six
figures.

Embeddings are cached to `.cache/embeddings/{corpus_version}.npy`, keyed by a content hash
of the corpus. Editing a rule invalidates the cache automatically — nothing goes stale
silently.

---

## 5. The deterministic core

`tools/waterfall.py` is where money is computed, and no model is anywhere near it.

```
gross
 → item deductions (Lists I–IV)      → payable_subtotal
 → room rent excess                   ┐
 → room rent proportionate            │ borne by PATIENT
 → procedure sub-limit                │
 → deductible                         │
 → co-pay                             │
 → balance sum insured cap            ┘
 = projected_settlement
```

### The invariant

```
projected_settlement + patient_liability + hospital_writeoff == gross_bill
```

Exactly — not approximately. It holds **by construction**: `patient_liability` is
`patient_items + sum(policy_deductions)`, `hospital_writeoff` is `hospital_items`, and
settlement is what remains after subtracting both from gross. Every deduction is quantised
to paise as it is recorded, and settlement is derived by subtraction rather than computed
independently, so rounding cannot drift the three apart.

This is the single most valuable test in the suite. It catches double-counting and
misattribution — the two ways this kind of engine goes quietly wrong — and it is checked
again at runtime by `verify_node`, which reports a mismatch as `ENGINE BUG` rather than
blaming the model.

### Why the proportionate deduction is configurable

Which charges count as "associated" varies by insurer, and it is the least understood rule
in Indian health insurance. Three profiles ship — conservative, typical, lenient — differing
only in which billing heads escape the deduction. The UI shows a **range** across all three.
Reporting one number would be inventing precision the domain does not have.

### The room downgrade simulator

`simulate_room_downgrade` deep-copies the packet, rewrites room line items to the cap rate,
re-runs the full waterfall, and returns the delta. On the cardiac sample it is worth
**₹87,916** — the single most persuasive output in the product, because it converts a policy
clause into a number a family would have wanted at admission.

---

## 6. Prompts

Six, each with one job. Full text lives beside the code that uses it.

| Prompt | Location | Output schema | Grounding requirement |
|---|---|---|---|
| `classify` | `nodes/classify.py` | `VerdictBatch` | must cite a real `chunk_id` for any LIST_* verdict |
| `explain` | `nodes/explain.py` | `Explanation` | may use only figures present in `facts_json` |
| `extract` | `nodes/extract.py` | `ExtractedBill` | transcribe only; never compute a total |
| `ocr` | `nodes/extract.py` | `ExtractedBill` | restore spacing, never invent a row |
| `text_to_sql` | `analytics.py` | `SQLAnswer` | SELECT only, allow-listed views |
| `judge` | `eval/judge.py` | `Judgement` | scores against the writer's exact fact set |

### Design principles common to all six

**Name the failure mode in the prompt.** The classify prompt says *"Retrieval always returns
something; if none of the candidates describe the item, answer PAYABLE or UNMAPPED"* because
the observed failure was the model treating a nearest neighbour as a match.

**Say what a field means, not just its type.** After the model confused `Room charges -
Single AC` (payable) with the air-conditioning surcharge (List II), the prompt gained an
explicit rule that `head` marks the primary service and only `head=OTHER` is a candidate for
a LIST_* class. That fix is also why the benchmark now carries a `head_only` control — see
[EVALUATION.md](EVALUATION.md).

**Enforce grounding in code, not prose.** The prompt asks for a citation; `classify_llm`
then checks the returned `chunk_id` against the real corpus and downgrades uncited LIST_*
verdicts to `UNMAPPED`. Asking politely is not a guardrail.

### Structured output without vendor lock-in

`LLMClient.structured()` is about forty lines:

1. `schema.model_json_schema()` into the system prompt
2. `response_format={"type":"json_object"}` **only if the provider supports it** — free
   community models often do not, and sending it rejects the whole request
3. `_json_only()` strips markdown fences and slices brace-to-brace
4. Validate; on `ValidationError`, append the assistant's reply *and the error text* and
   retry, bounded at 2
5. Cache the validated result by content hash

`instructor` would have done steps 1–4. It was rejected so the same code works unchanged
against Groq, DeepSeek, OpenRouter and OpenAI — and step 2's capability flag is exactly the
kind of provider difference a wrapper would have hidden until it broke.

---

## 7. Persistence

Three tables, SQLite, no ORM. See [DECISIONS.md](DECISIONS.md) for why not Postgres.

| Table | Grain | Why it exists |
|---|---|---|
| `claims` | one row per audited claim | portfolio totals, month trends, agent/deterministic split |
| `findings` | one row per bill line | the leakage analysis — group by `bearer` and `classification` |
| `doc_gaps` | one row per missing document per claim | "most-missed document" ranking |

Normalised to 3NF with one deliberate exception: `claims.room_rent_deduction_paise` is
derivable by summing `policy_deductions`, but those are not persisted individually. Storing
the rollup avoids a table whose only consumer is one dashboard tile.

### Money is integer paise

```sql
gross_bill_paise INTEGER NOT NULL
```

The engine is `Decimal` end to end. Persisting to SQLite `REAL` would discard that at the
final boundary. At 302 rows the aggregate drift is ~1e-10 and invisible in rupee display —
so this is a consistency fix, not a live bug. But "money is never float" is a claim the
README makes, and it should be true everywhere or not be claimed.

Rupee **views** sit on top:

```sql
CREATE VIEW v_claims AS SELECT ..., gross_bill_paise / 100.0 AS gross_bill, ... FROM claims;
```

Reporting and text-to-SQL read the views, so generated SQL keeps rupee semantics and answers
come back as `24300` rather than `2430000`. Storage stays exact; readers get natural units.
The text-to-SQL allow-list contains only the views, so generated SQL cannot accidentally
read raw paise and report a figure 100× too large.

### Indexes

`findings(classification)`, `findings(bearer)`, `claims(month)`, `doc_gaps(severity)` — the
four columns every dashboard aggregate groups by. Honestly: at 302 claims (~6,000 findings)
SQLite scans the table faster than it consults an index, so these are precautionary. They
begin to earn their place around 10,000 findings, roughly 500 claims.

### Foreign keys

`findings.claim_id` and `doc_gaps.claim_id` declare
`REFERENCES claims(claim_id) ON DELETE CASCADE`. **SQLite ignores foreign keys unless
`PRAGMA foreign_keys = ON` is set per connection** — a declared constraint without the
pragma is decoration. `connect()` sets it, and `test_store.py` asserts an orphan insert
actually raises.

### Migration

`PRAGMA user_version` gates a drop-and-rebuild. Every row is reproducible from
`data/generated/portfolio` in about thirty seconds, so a real migration path would be
ceremony for regenerable data.

One trap, found by a failing test: `user_version` is **0** for a pre-versioning database.
Writing the check as `if version and version < SCHEMA_VERSION` treats 0 as falsy and skips
migration on exactly the stale databases that need it. There is now a regression test.

---

## 8. The API

18 endpoints. The live, generated reference is at `http://127.0.0.1:8000/docs` — FastAPI
builds it from the same Pydantic models the code uses, so it cannot drift. A hand-maintained
copy here would.

| Group | Endpoints |
|---|---|
| Health | `GET /health` — provider, model warnings, corpus version, retrieval status |
| Providers | `GET /api/providers`, `POST /api/providers/{name}` — live switch |
| Config | `GET /api/profiles` — the three insurer profiles |
| Samples | `GET /api/samples`, `GET /api/samples/{name}` |
| Audit | `POST /api/audit`, `POST /api/simulate-room` |
| Ingest | `POST /api/extract-pdf` |
| Output | `POST /api/report` (PDF), `GET /api/trace/{claim_id}` |
| Corpus | `GET /api/rules`, `GET /api/rules/{chunk_id}` |
| Analytics | `.../summary`, `.../leakage`, `.../top-items`, `.../missing-docs`, `POST .../ask` |

Everything except analytics is **stateless** — `POST /api/audit` takes a full `ClaimPacket`
and returns a full `AuditResult`. No session, no server-side claim store in the request
path. Persistence is a side effect for the dashboard, not part of the contract.

`GET /api/rules/{chunk_id}` exists so the Trace view can expand a citation into the actual
corpus text. Grounding you cannot inspect is a claim, not a guarantee.

---

## 9. Scalability — what breaks first

Honest ordering, at the point each becomes the binding constraint:

**1. LLM rate limits (immediate).** Already the binding constraint. A full agent run is
~2,400–3,900 tokens; Groq's free tier is ~200k/day per *organisation* — a second key on the
same account shares the pool. Batching classification at 12 items per call was the main
lever. Beyond that: a paid tier, or the deterministic path, which needs no key at all.

**2. Vision token cost (immediate, on scans).** The vision model is billed a flat ~11,111
tokens per image against an 8,000/minute cap — one image exceeds the entire allowance, and
halving the resolution changed nothing because the rate is flat. Resolved by routing around
it: `pdfplumber` for native PDFs (100% row recall), local RapidOCR for scans, vision only if
both fail.

**3. Retrieval (~50k chunks).** Brute-force cosine is O(n·d) per query. At 104 chunks it is
microseconds; at 50k it becomes tens of milliseconds and an approximate index starts paying
for itself.

**4. SQLite writes (~thousands of concurrent audits).** One writer at a time. Reads scale
fine. The dashboard is read-only, so this only binds if many users audit simultaneously —
at which point the answer is Postgres, and `store.py` is the only module to change.

**5. Streamlit (~dozens of concurrent users).** Single process, one script run per
interaction. The API is already separate, so the UI could be replaced without touching the
engine.

Not on this list: the corpus, the waterfall, the consistency checks. All are microseconds
and none scale with usage.

---

## 10. Alternatives rejected

| Rejected | For | Why |
|---|---|---|
| ChromaDB | numpy | 20 lines vs a dependency with conflicting pins, at 104 chunks |
| sentence-transformers | fastembed | same embeddings, ONNX, ~130 MB not ~2 GB, no PyTorch |
| Tesseract / EasyOCR | RapidOCR | no system binary, no torch, runs on onnxruntime already present |
| `instructor` | 40 lines | works identically across four providers; capability flags stay visible |
| Cross-encoder reranker | RRF | a reranker means torch, for an unmeasured gain |
| YAML rules + `simpleeval` | Python predicates | a parser and a sandbox-escape question to replace a dozen readable predicates |
| Postgres | SQLite | 302 rows, one writer, embedded reads |
| Env vars per provider | `providers.json` | four values that must change together is a footgun |
| `hypothesis` | 3 plain assertions | same bug-catching, none of the setup |

Each of these is a decision record in [DECISIONS.md](DECISIONS.md) with the full trade-off.

---

## 11. Where this would break on real data

Stated plainly, because the limitations are the honest part:

- **The corpus is unverified.** 104 rules written from publicly circulated non-payable lists,
  never checked against a current IRDAI circular. Stamped `unverified-*` and surfaced in the
  UI and on every PDF.
- **All data is synthetic.** Real bills have inconsistent department naming, merged cells,
  handwriting and hospital-specific abbreviations. The `head` field is far cleaner in
  generated data than it would ever be in practice — which is precisely the leak
  [EVALUATION.md](EVALUATION.md) documents.
- **The insurer profiles are approximations.** Three plausible interpretations of the
  associated-charge list, not any real insurer's rulebook. Hence a range, not a number.
- **Auth is off by default, not absent.** Authentication, tenancy and the audit ledger
  exist and are tested, but only activate when configured — `CLAIMIQ_API_KEYS` enables
  API-key auth with per-tenant scopes, and the ledger records who asked. Exposing it
  publicly with real claim data requires the operator to enable them first; a public bind
  with no keys is flagged `insecure_deployment`.
