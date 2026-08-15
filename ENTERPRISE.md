# ClaimIQ — the operational layer

`ARCHITECTURE.md` explains how the *determination* is made: the graph, the retrieval,
the deterministic waterfall, the invariant. This document is about everything around
it — who is allowed to ask, what gets recorded, what happens at four thousand claims
instead of one, and what still has to be built before a hospital group's finance team
could run their book through it.

It is in three parts: the stack as it stands, the layer added on top of it, and the
roadmap with the honest gaps named.

---

## 1. The stack

| layer | choice | why this and not the obvious alternative |
|---|---|---|
| orchestration | LangGraph | Only for the backward edge (`verify → explain`). Nodes are plain `ClaimState -> dict` functions and none of them imports langgraph, so the framework is swappable without touching a node. |
| models | any OpenAI-compatible endpoint | Groq / DeepSeek / OpenAI / OpenRouter differ by base URL and model id. Named profiles in `providers.json`, switchable at runtime because free-tier quotas run out mid-demo. |
| structured output | Pydantic + validation retry | `response_format` is not universally supported; declared per provider, with a targeted re-prompt carrying the validation error when it is absent. |
| retrieval | BM25 + `fastembed` dense, fused with RRF | No vector database. 104 chunks. An in-process NumPy dot product is the correct data structure at this size; a Qdrant container would be infrastructure to make a demo look bigger. |
| OCR / extraction | `pdfplumber` text layer → `rapidocr-onnxruntime` → vision model | Three paths in falling order of trustworthiness. The text layer is exact and free; the vision model is the last resort and its measured row recall (81.1%) is disclosed rather than hidden. |
| money | `Decimal` end to end, integer paise at rest | Never float, including in the SQL views — they use integer division and expose the paise column alongside so readers can reassemble exact Decimals. |
| persistence | SQLite, WAL | Two databases with opposite rules: `claimiq.db` is mutable current state, `ledger.db` is append-only history. See §2.3. |
| API | FastAPI, `/api/*` + `/v1/*` | The UI's surface and the integrator's surface are different promises, so they are different prefixes. |
| UI | Streamlit + a static SPA | Streamlit for the analyst screens; `web/` for the landing and audit views, pinned to real engine output by `tests/test_landing.py`. |
| eval | `scripts/bench_*.py` → committed markdown | Every number in the README regenerates. None is hand-typed. |

**Runtime shape.** One Python process (or two locally — `start.ps1` runs API and UI
separately; `ui/_boot.py` co-locates them on single-port hosts). No queue, no cache
server, no container orchestration. That is a deliberate stopping point, and §3 says
what replacing each piece involves.

---

## 2. What the operational layer does now

### 2.1 Identity — `claimiq/tenancy.py`

API keys as `key:tenant:scopes`, in one environment variable. Three scopes
(`read` ⊂ `audit` ⊂ `admin`), coarse on purpose — fine-grained permissions nobody
asked for multiply the states an operator can get wrong, and the states they get wrong
grant too much.

Two properties worth stating:

- **Authentication is off until a key is configured, and `/health` says so.** A
  security layer that breaks `.\start.ps1` for someone who just cloned the repo is a
  security layer that gets deleted. What must never happen is it being off *silently*.
- **The key never reaches a log line or a ledger row.** A `Principal` carries
  `key_id` — the first 12 hex of the key's SHA-256 — which distinguishes two keys and
  cannot replay either.

Comparison is `hmac.compare_digest`, over every configured key rather than returning
on first match, so neither the value nor the position leaks through timing.

Rate limiting is a sliding window per principal, in-process. Sliding rather than fixed
because a fixed window lets a caller send two full budgets across the boundary, which
is exactly the burst that knocks over a synchronous OCR endpoint. **It bounds one
worker, not a cluster** — with replicas behind a load balancer this becomes
per-replica, and the real answer is Redis (§3).

### 2.2 Request lifecycle — `claimiq/observability.py`

Every request gets a correlation id (honoured from `X-Request-ID` if the caller sent
one, so a trace can span two services), echoed on the response and attached to every
log line and every 500 body. A user reporting a failure hands over one string.

Metrics are Prometheus text exposition at `/metrics`, hand-rolled rather than pulling
in `prometheus-client` — the format is a dozen lines and the registry is a dict, so
the dependency would cost more than the code. Latency buckets are chosen for what this
service actually does: a deterministic audit ~50 ms, an LLM audit in single seconds,
OCR ~15 s a page, so the interesting range spans three orders of magnitude.

Metric labels use the **route template**, not the concrete path — `/api/claims/{claim_id}`,
never `/api/claims/SYNTH-0042`. One time series per claim id is the classic way to
melt a metrics backend, and it copies claim identifiers into a store that is usually
far less protected than the database they came from. Pinned by a test.

`CLAIMIQ_JSON_LOGS=true` switches to one JSON object per line. Every aggregator parses
that; none reliably parses uvicorn's default.

### 2.3 The audit ledger — `claimiq/ledger.py`

The piece that most changes what this product *is*.

`store.py` holds the current portfolio — one row per claim, overwritten on re-audit.
Right for a dashboard, wrong for the question that eventually gets asked: *on what
basis did you tell us, in March, that this claim would settle at ₹3.1 lakh?* Answering
needs the input as it was, the corpus version as it was, and the answer as it was, and
none of those survives an overwrite.

So a second store with opposite rules. Never updated, never deleted. Each entry
carries the SHA-256 of the previous one, so the sequence is tamper-evident:
`GET /v1/ledger/verify` walks the chain and names the sequence number where it breaks,
distinguishing *an entry was edited* from *an entry was removed or reordered*.

This is not a blockchain and does not pretend to be — anyone with write access can
rewrite the chain from the break onward. What it buys is that they cannot do it
*quietly*, and pinned against an offsite copy of the head hash it becomes genuinely
hard.

**What is recorded, and what deliberately is not:**

| recorded | not recorded |
|---|---|
| claim id, tenant, key fingerprint, request id | the packet itself |
| corpus version, engine strategy, verdict | patient names |
| the three money figures, finding counts by class | free-text diagnoses |
| SHA-256 of the input packet | line-item descriptions |

The digest is the compromise. It proves *which* input produced this answer — re-hash
the packet you hold and compare — without the compliance store becoming a second
uncontrolled copy of the clinical record. A store that accumulates PHI is a liability,
and none of the entries that make this useful need any.

Two details that matter:

- The digest canonicalises with sorted keys, so re-verifying an entry a year later
  does not fail because the JSON was serialised in a different order.
- `record()` takes the write lock with `BEGIN IMMEDIATE` before reading the head.
  Without it, two batch workers finishing simultaneously both read head *N*, both
  write `prev_hash = N`, and fork the chain — which `verify_chain()` would report as
  tampering when it was only a race.
- A ledger failure appends to `result.errors` and never fails the audit. A compliance
  sink that can take down the answer turns a full disk into an outage.

### 2.4 Batches — `claimiq/jobs.py`

`POST /v1/batches` accepts up to 500 claims and returns `202` with a job id. A bounded
pool works through them; `GET /v1/batches/{id}` reports progress and running totals.

`202` rather than `200` is not pedantry — the response describes work *accepted*, not
work *done*, and a client that conflates them will read `settlement: 0` off an empty
job and believe it.

Partial failure is first-class: one malformed claim fails that claim and records the
error against its own index; the other 499 complete. Each claim runs in its own copied
context, so its trace and token ledger are its own — auditing in a bare loop would
append every claim's nodes to the first claim's trace.

Cross-tenant reads return **404, not 403**. A 403 confirms the job id exists, which is
a small enumeration oracle.

**In-process and not durable.** A restart loses in-flight jobs. Deliberate stopping
point rather than oversight: the durable version is Celery or arq on Redis, that is a
deployment decision, and building half of it here would produce something that looks
durable and is not.

### 2.5 Concurrency defects this exposed

Three real bugs, none of which any existing test could see, all of which only appear
under two simultaneous requests. FastAPI runs `def` endpoints in a threadpool, so
"simultaneous" was already reachable — the batch endpoint just made it routine.

1. **`trace.py` kept the current run in a module global.** Two concurrent audits both
   wrote to it; the second discarded the first's partial trace and every node that
   finished afterwards appended to the wrong claim. The failure is silent and
   asymmetric — *the numbers stay right and the provenance stops being true*, which is
   the worst thing to be wrong in an auditor. Now a `ContextVar`.

2. **`LLMClient.calls` was one shared list.** `try_client()` is `lru_cache`d so every
   node shares one instance — which is what makes token attribution work at all — but
   `trace.track` attributes usage by slicing that list, so claim A's slice picked up
   claim B's calls. Now a context-scoped ledger, reset per run.

3. **SQLite ran in rollback-journal mode.** One writer blocked every reader, so a
   dashboard query issued while a batch persisted failed outright with *database is
   locked* instead of waiting. Now WAL with a 10 s busy timeout — readers proceed
   against the last committed snapshot while one writer appends.

Both context fixes are pinned by tests that force the interleave with a `Barrier`
rather than hoping for it.

### 2.6 Text-to-SQL hardening

`/api/analytics/ask` takes free text from whoever can reach it and feeds it to
something that writes SQL. The guardrails were structural already; they had two gaps
in opposite directions.

- **Comments and string literals are stripped before the keyword check.** Scanning raw
  text meant `WHERE description LIKE '%drop foot%'` was refused as dangerous, and a
  query annotated `-- drop the tiny ones` likewise. Stripping first makes the check
  describe what will actually execute.
- **CTEs are recognised.** Every trend question the model writes starts
  `WITH monthly AS (...)`, and `monthly` was being counted as an unknown table — so
  the most common useful query shape was the one that always failed.
- **`LIMIT` is imposed, not requested.** The prompt asks for 50; the code appends 200
  if absent. A model following an instruction is not a resource control, and a
  cartesian join across `v_claims` and `v_findings` is one token away at all times.
- **A wall-clock ceiling via SQLite's progress handler.** `busy_timeout` does not help
  a query that is *making progress* and will not finish this decade; the progress
  callback is the only thing that interrupts it.
- Questions over 500 characters are refused. Not a complete defence against prompt
  injection — `validate_sql` is — but there is no legitimate 4,000-character question
  about a five-column schema.

### 2.7 Upload limits

`Content-Length` is used as a cheap early reject, then the real limit is enforced on
the bytes as they arrive. Reading first and checking after means a 2 GB upload is
already resident in the worker by the time it is refused — a one-line denial of
service against a synchronous OCR endpoint.

### 2.8 New surface

```
GET  /metrics                      Prometheus exposition (public, like /health)
GET  /v1/metrics.json              same numbers as JSON             read
POST /v1/batches                   submit up to 500 claims → 202    audit
GET  /v1/batches                   jobs for this tenant             read
GET  /v1/batches/{id}              progress, outcomes, totals       read
POST /v1/batches/{id}/cancel       stop after the in-flight claim   audit
GET  /v1/ledger?claim_id=          entries + current head hash      read
GET  /v1/ledger/verify             walk the chain                   admin
```

Existing `/api/*` routes are unchanged in shape; the mutating ones now carry a scope.
All 191 pre-existing tests pass untouched, plus 39 new ones.

---

## 3. What is still missing for enterprise

Ordered by what blocks a real deployment first. Each names the actual gap rather than
a technology.

### Tier 1 — blocks a paying pilot

**Postgres, and tenant isolation in the schema.** SQLite is one writer and one file.
`claim_id` is a global primary key, so two hospitals with a `CLM-001` collide. The
work is: `tenant_id` on every table, composite keys, row-level security, and
Alembic migrations — the ledger in particular must migrate rather than rebuild, since
its whole value is that it was not rebuilt.

**Encryption at rest and a real key store.** Bills carry names, diagnoses and dates.
Today they sit in a plain SQLite file and API keys sit in an environment variable.
Needs: encrypted volumes or column encryption for the PII columns, and keys in Vault /
KMS / Secrets Manager with rotation. The ledger's PHI-free design means it does *not*
need this, which was the point of designing it that way.

**Durable jobs.** Celery or arq on Redis, with retries, dead-letter, and a job table
that survives a restart. The `JobRegistry` interface is deliberately narrow —
`submit` / `get` / `list` / `cancel` — so the swap is one module.

**SSO and real identity.** Static API keys are right for service-to-service and wrong
for humans. OIDC against the hospital's Entra/Okta tenant, group→role mapping, and
short-lived tokens. `Principal` is already the single choke point every route reads.

**A data-retention policy that is code.** How long a claim packet is kept, when
extracted line items are purged, what a deletion request does to the ledger (the
answer is: tombstone the entry, never break the chain). Currently nothing expires.

### Tier 2 — makes it operable

- **Distributed rate limiting** — the current limiter is per-process. Redis token
  bucket, keyed by principal.
- **Idempotency keys** on `POST /api/audit` and `/v1/batches`, so a client retry after
  a timeout does not double-charge the ledger.
- **Webhooks** — `POST /v1/batches` with a callback URL beats polling for a 4,000-claim
  run. Needs HMAC-signed payloads and a retry ladder.
- **OTLP from `trace.py`** rather than a second tracing mechanism alongside it. The
  per-claim trace already records exactly what anyone debugging this wants; it should
  emit spans, not be duplicated by OpenTelemetry auto-instrumentation.
- **Corpus versioning as a first-class object** — the ledger records `corpus_version`,
  but there is no endpoint that returns *the corpus as it was at version X*. Without
  that, reproducing a March determination is still manual.
- **A staging→production corpus promotion flow.** Rule changes are currently a git
  commit. They should be: propose, diff against the labelled benchmark, show the
  classification delta, then promote.

### Tier 3 — product

- **Payer feedback loop.** The system estimates deductions; it never learns what the
  insurer actually deducted. Ingesting settlement advices and scoring predictions
  against them turns every claim into a labelled example and turns "estimated
  settlement" into a calibrated number with a confidence interval.
- **Per-insurer rule profiles**, learned from that feedback rather than hand-configured.
  `PROFILES` is already the seam.
- **Pre-authorisation mode** — run the same engine on the *proposed* treatment plan
  before admission, where the patient-liability number is worth most.
- **Denial-letter drafting**, grounded in the same cited chunks the audit used, so the
  appeal cites the rule rather than asserting it.
- **Hospital-side coding QA.** Lists II/III/IV are the hospital's own billing errors;
  the top-leaking-items report already identifies the repeat offenders. The next step
  is fixing the charge master, not re-detecting the same error every month.

### What would break first, and at what scale

| load | first failure | fix |
|---|---|---|
| ~20 concurrent audits | Groq TPM ceiling → deterministic fallback | paid tier, or per-tenant provider keys |
| ~50 req/s | uvicorn threadpool saturated by synchronous OCR | move extraction to the job queue |
| ~500 concurrent readers | SQLite WAL reader limit | Postgres |
| ~1M ledger entries | `verify_chain()` is O(n) full walk | checkpoint hashes every 10k, verify the segment |
| multi-region | in-process rate limiter and job registry | Redis for both |

---

## 4. Configuration

Every setting defaults to the single-user local demo. `.env.example` documents all of
them; the ones that change posture:

```bash
CLAIMIQ_API_KEYS=k_live_apollo_9f3a2b8c:apollo:audit,k_ro_maxhc_44de:maxhc:read
CLAIMIQ_RATE_LIMIT_PER_MINUTE=120
CLAIMIQ_MAX_UPLOAD_BYTES=26214400
CLAIMIQ_MAX_BATCH_SIZE=500
CLAIMIQ_LEDGER=true
CLAIMIQ_JSON_LOGS=true
```

`GET /health` reports the resulting posture — whether auth is on, how many principals,
which tenants, and any malformed key entries — without naming a secret.
