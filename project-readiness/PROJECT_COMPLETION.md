# ClaimIQ — Project Completion Report

**Date of this pass:** 2026-08-11
**Scope of this pass:** full-codebase audit, end-to-end verification of every layer
(backend engine, API, database, frontend), targeted fixes to close real gaps, and this
document. Written from the actual, running code — every number and claim below was
observed directly (test run, live API calls, live browser session), not copied from
older docs.

---

## 1. What the product does

ClaimIQ is a **pre-submission auditor for Indian hospital insurance claims**. A hospital
billing desk uploads (or types in) a claim packet — bill line items, policy terms, room
stay, clinical context — before it goes to the insurer/TPA, and gets back:

1. **Three reconciling numbers**: estimated settlement, patient liability, and hospital
   write-off, computed across three insurer-profile interpretations (conservative /
   typical / lenient) so the output is a defensible range, not a fake-precise guess.
2. **Line-by-line verdicts** on every bill item — payable, or non-payable under one of
   four IRDAI-style lists — each with a citation to the rule it came from.
3. **Who absorbs each deduction.** This is the product's core insight: List I
   (optional items like food/telephone/laundry) is the *patient's* cost; Lists II/III/IV
   (gowns, gloves, drapes, registration fees, etc.) are charges the hospital billed and
   will never be paid for — a repeating billing-master error, not a one-off. A naive
   "non-payable items" checker collapses this distinction; ClaimIQ's whole value is
   surfacing the second, more expensive, more fixable category.
4. **Missing-document and consistency warnings** — what will bounce the claim back for
   query before it's even submitted.
5. **A portfolio dashboard** — across every audited claim, which billing habits leak the
   most money, ranked, so a hospital can fix its top 3 recurring mistakes instead of
   re-discovering the same error every month.

It explicitly **estimates, does not adjudicate** — the rule corpus is stamped
`unverified-*` and the UI says so on every screen and PDF. All bundled data (bills,
claims, corpus) is synthetic; no real patient data has ever touched this system.

---

## 2. Current architecture and stack

```
Browser
  │
  ├─► frontend/  Next.js 14 (App Router, client-rendered) — PRIMARY UI
  │      talks to the API directly over HTTP/JSON
  │
  └─► web/       bundled static SPA — fallback UI baked into the API's own container
         (also: ui/ — the original Streamlit UI, kept for side-by-side comparison)

claimiq/api.py        FastAPI — 33 endpoints (see §7)
  claimiq/graph.py       LangGraph agent — classify → compute → readiness → explain
                          → verify ⇄ (repair, max 2) → report
  claimiq/nodes/         one plain `ClaimState -> dict` function per stage; nothing in
                          nodes/ imports langgraph — the framework is swappable
  claimiq/tools/         deterministic Decimal waterfall, consistency checks, documents
  claimiq/retrieval/     BM25 + fastembed dense + Reciprocal Rank Fusion over corpus/
  claimiq/llm.py         OpenAI-compatible client, JSON-mode capability flag, disk cache
  claimiq/store.py       SQLite (claims / findings / doc_gaps), integer-paise money
  claimiq/ledger.py      append-only, hash-chained audit ledger (separate DB)
  claimiq/jobs.py        in-process batch-audit job queue (up to 500 claims/request)
  claimiq/tenancy.py     API-key auth (off by default), scopes, rate limiting
  claimiq/idempotency.py Idempotency-Key handling for /api/audit and /v1/batches
  claimiq/webhooks.py    HMAC-signed webhook delivery (library, no route of its own)
  claimiq/retention.py   data purge / right-to-erasure endpoints
  claimiq/observability.py  correlation IDs, Prometheus metrics, structured logs
corpus/                104 hand-authored markdown chunks, the rule knowledge base
data/                  claimiq.db (portfolio), ledger.db (compliance), seeded samples
```

**Stack verdict: keep it, it is already right-sized.** This was independently verified,
not assumed:

- **FastAPI + Pydantic v2** is the actual contract — `/docs` is generated from the same
  models the code runs, so it can't drift. Confirmed live.
- **LangGraph** earns its dependency for exactly one thing — the bounded `verify ⇄
  explain` repair cycle — and every node stays a plain function, so the framework
  is provably swappable (~30 lines) if it ever became a liability. It hasn't.
- **SQLite**, not Postgres, is correct at this data volume (301 claims, one writer,
  embedded reads) and is a documented, reasoned decision (`DECISIONS.md` ADR-003), not
  an unexamined default. It does become the right thing to swap the moment there are
  multiple concurrent writers or multi-tenant isolation requirements — see §12.
- **numpy** cosine search instead of a vector database is correct at 104 corpus chunks
  (a 160 KB matrix) and does not need reconsidering below roughly five figures of chunks.
- **Next.js 14 (App Router, client components)** for the frontend is a reasonable,
  unglamorous choice — it builds cleanly, typechecks cleanly, and every page does real
  data fetching with TanStack Query (loading/error/empty states throughout, verified by
  reading every screen's source, not assumed).

Nothing here needed replacing. The gaps found in this pass (§5, §11) were **integration
and documentation gaps**, not architectural ones.

---

## 3. What was already implemented (before this pass)

This was a mature codebase, not a prototype — verified by running it, not by reading
its own claims:

- The full 6-node agent graph, deterministic waterfall, hybrid retrieval, and the
  citation/invariant guardrails — all working, all tested.
- A complete FastAPI backend with 33 endpoints: audit, PDF report, sample claims, rule
  catalog, portfolio analytics + NL-to-SQL, batch jobs, an append-only audit ledger,
  data retention/erasure, Prometheus metrics, and API-key tenancy — this is a real
  "operational layer" (`ENTERPRISE.md`), not a toy backend.
- A complete Next.js frontend (`frontend/`) with 5 routed pages, all wired to real API
  calls, replacing an earlier Streamlit-only UI (per commit history: "Replace the
  Streamlit UI with a real SPA").
- A Docker + Render + Vercel deployment path (`Dockerfile`, `docker-entrypoint.sh`,
  `render.yaml`, `DEPLOY.md`) with a seed-once-on-first-boot entrypoint that protects a
  live ledger/database from being clobbered by redeploys.
- 230 tests (grown from an original 59 as the operational layer was added), all offline,
  no API key required.
- Extensive, honest documentation: 14 numbered engineering decision records with
  measured trade-offs (`DECISIONS.md`), a full evaluation methodology with published
  failure analysis (`EVALUATION.md`), and a documented list of exactly what would need
  to be built before this could serve real patient data (`ENTERPRISE.md` §3).

## 4. What was partially implemented / what was broken

Nothing in the **engine or API** was broken — every backend code path exercised in this
pass worked correctly on the first try. What was incomplete was **integration and
documentation currency**, specifically:

| Gap | Where | Severity |
|---|---|---|
| `README.md` never mentioned `frontend/` at all — it still described the old single-process Streamlit quick start (`:8501`) as if it were the only UI | `README.md` | High — this is the first file anyone reads |
| `ConnectionGuard` component (a full "backend unreachable" screen) was written but never imported anywhere — a dead backend would instead leave every page's queries spinning forever with no explanation | `frontend/src/components/connection-guard.tsx` | Medium — real UX gap, would confuse a demo audience |
| The API's own "no UI found" fallback route still told a visitor the UI was "the Streamlit app on :8501", with no mention of the frontend that has existed for several commits | `claimiq/api.py` (unreachable in the current deployment since `web/index.html` exists, but wrong if `web/` is ever removed) | Low |
| README's test count (`59 tests`) and endpoint count (`18 endpoints`, in `ARCHITECTURE.md`) predate the enterprise-layer commit and are stale (actual: 230 tests, 33 endpoints) | `README.md` | Low — cosmetic, but a stale number undermines the credibility of the true ones |

All four are fixed in this pass (§5). No functional backend or frontend defect was found
— the engine, API, and every frontend screen worked correctly against real data on first
verification.

## 5. What was completed / fixed in this pass

1. **Wired `ConnectionGuard` into `AppShell`** (`frontend/src/components/app-shell.tsx`)
   so a backend outage now shows a clear "Cannot reach the ClaimIQ service" screen with
   a retry button and the exact command to restart the API, instead of every page
   spinning silently forever. Verified in the browser (no regression — the guard is
   correctly inert while the backend is healthy) and the production build still passes.
2. **Fixed the stale fallback text** in `claimiq/api.py`'s `/` route to mention the
   frontend dev command first, with the Streamlit path labelled legacy.
3. **Rewrote the README** quick-start, screens table, test count, and documentation
   index to describe the system as it actually runs today: the API standalone, the
   Next.js frontend as the primary UI (`npm run dev` → `:3000`), and the two other UIs
   (`web/` bundled static site, `ui/` legacy Streamlit) named explicitly as
   still-working alternates rather than left silently undocumented. Added links to
   `DEPLOY.md`, `ENTERPRISE.md`, and this document.
4. **Verified, end-to-end, with real network calls** (not assumed from reading code):
   backend test suite, backend live smoke tests, frontend production build, and a full
   browser walkthrough of all 5 frontend pages against the live backend — see §13.

No architectural changes were made. No dependency was added, removed, or upgraded. No
working code was rewritten — the fixes above are the entire diff.

---

## 6. End-to-end user workflows (all verified live in this pass)

**Audit a new claim (primary flow).**
Browser (`http://localhost:3000`) → user drops a PDF or clicks a sample button →
`POST /api/extract` (native PDF: `pdfplumber` table parse; scan: on-device RapidOCR →
LLM structuring) → `ClaimForm` pre-fills → `POST /api/audit` → LangGraph runs
`classify → compute → readiness → explain → verify` (with bounded repair) →
`AuditResult` renders as a verdict banner, three-way split (settlement/patient/hospital),
per-line findings with rule citations, missing-document list, narrative, and a
downloadable PDF (`POST /api/report`) — and the result is persisted to SQLite for the
dashboard. **Ran live in this pass** on the bundled cardiac sample: gross ₹4,10,000 →
settlement ₹2,27,700 / patient ₹1,74,550 / hospital ₹7,750 (sums exactly to gross), 23
findings, `verify_passed: true`, 0 repairs needed, narrative and action list both
generated by the real LLM (`groq / openai/gpt-oss-120b`). This exact result matches the
worked example documented in `DOCUMENTATION.md`, confirming the shipped engine still
produces the numbers the docs claim.

**Browse claim history.** `/claims` → `GET /api/claims` (search + month filter,
paginated) → click a row → `/claims/{id}` → `GET /api/claims/{claim_id}` for the
persisted figures, findings, and document gaps. Verified live against the 301-claim
seeded portfolio plus the fresh audit from the flow above (which appeared in the list
immediately after running).

**Portfolio dashboard.** `/dashboard` → four parallel queries
(`summary`/`leakage`/`top-items`/`missing-docs`) → stat cards, a Recharts leakage-by-cause
bar chart, top-10 recurring billing errors, top-10 most-missed documents. Verified live:
301 claims, ₹1,17,58,32 preventable hospital write-off, correct top offenders (CSSD
Charges, OT Drape Sterile, etc.).

**Rule catalog.** `/rules` → `GET /api/rules?full=true` → all 104 corpus chunks,
searchable, grouped by list, with the "unverified snapshot" banner rendered because
`verified_on` is genuinely `null`. Verified live.

**Portfolio-scale operations (API-only, no frontend screen yet).** `POST /v1/batches`
accepts up to 500 claims and returns `202` with a job id; `GET /v1/batches/{id}` reports
progress; the audit ledger (`GET /v1/ledger`, `GET /v1/ledger/verify`) provides a
tamper-evident record of every determination made, independent of the mutable
`claims` table. These were not re-tested live in this pass (no realistic 500-claim batch
was assembled) but are covered by the 39 enterprise-layer tests, which passed.

---

## 7. Major features that actually work (verified, not assumed)

- Full agentic audit pipeline with real LLM calls (classify, explain) and deterministic
  guardrails (compute, verify) — confirmed live, invariant held exactly.
- Bounded repair loop (`verify → explain`, max 2) — implemented and unit-tested
  (`tests/test_verify.py`); not forced to fire in this pass's live run because the first
  narrative passed verification (itself a good sign, not a gap).
- Hybrid retrieval (BM25 + dense + RRF) grounding every classification with a citable
  chunk ID.
- Deterministic waterfall across 3 insurer profiles, always summing exactly to the gross
  bill — this is the one invariant the whole product's credibility rests on, and it held
  on every live check performed.
- PDF extraction (native table parse) and audit-report PDF generation.
- Portfolio persistence and analytics (dashboard, claims list/detail).
- Rule corpus browsing with citation lookup.
- 33-endpoint API surface including auth/tenancy (off by default, but implemented and
  tested), rate limiting, an append-only compliance ledger, batch jobs, idempotency keys
  on mutating endpoints, data retention/erasure, and Prometheus metrics.
- A production Next.js frontend that builds cleanly (`npm run build` — compiles,
  typechecks, lints, prerenders 8 routes, zero errors) and runs cleanly against the live
  backend with zero console errors across every page.

## 8. Frontend/backend integration (verified by direct cross-reference, not inference)

Every one of the 15 API calls the frontend's `lib/api.ts` makes was cross-checked
against a real route in `claimiq/api.py` — all 15 match an existing endpoint exactly, no
frontend call targets a nonexistent route. Conversely, the frontend does not yet have
screens for a substantial and genuinely useful slice of backend surface: provider
switching, the room-downgrade simulator, the LangGraph execution trace viewer, NL-to-SQL
analytics ("Ask"), batch jobs, the ledger, and retention. None of this is broken — it is
backend capability the frontend hasn't grown UI for yet, most of it inherited from the
Streamlit UI which does still expose Trace and Ask. This is the most legitimate "next
feature work" item in the repo (see §11).

TypeScript response types in `frontend/src/lib/types.ts` were spot-checked against the
Pydantic models (`AuditResult`, `WaterfallResult`, `ItemFinding`, `DocumentGap`,
`ConsistencyFlag`) and the SQL projections in `store.py` — all match on every field the
frontend actually reads. `DocumentGap`/`ConsistencyFlag` are typed slightly narrower than
the backend's full `Finding` base class, but the frontend never reads the missing
fields, so this is a latent tightening opportunity, not a bug.

## 9. Database / data flow

- **`data/claimiq.db`** — SQLite, WAL mode, 3 tables (`claims`, `findings`, `doc_gaps`)
  plus rupee-denominated views (`v_claims`, `v_findings`) over integer-paise columns, so
  money is `Decimal` end-to-end including at rest — confirmed by reading `store.py` and
  by the live audit persisting and round-tripping correctly through the dashboard.
- **`data/ledger.db`** — separate, append-only, hash-chained compliance record,
  deliberately PHI-free (records figures and a SHA-256 of the input packet, never the
  packet itself).
- 301 claims are currently seeded (300 synthetic + the one live cardiac audit run during
  this verification pass), backing the dashboard and claims list with real, non-trivial
  aggregate numbers rather than a handful of placeholder rows.
- Foreign keys are enforced (`PRAGMA foreign_keys = ON` set per connection, tested).
- Migration is `PRAGMA user_version`-gated drop-and-rebuild — appropriate for fully
  regenerable seed data; **not** appropriate once a real ledger exists, which is why the
  ledger is a separate, non-rebuilt database.

## 10. Authentication / security status

**Auth is off by default** and the running system in this pass had `auth_enabled: false`
(confirmed via `GET /health`) — this is correct for local development and a personal
demo, and is explicitly, repeatedly documented as *not* production-hardened for real
patient data (`README.md` "Security and privacy", `DEPLOY.md` §4, `ENTERPRISE.md` §3).

What **is** implemented and tested, ready to switch on:

- API-key auth (`CLAIMIQ_API_KEYS=key:tenant:scopes`), constant-time comparison,
  3-tier scopes (`read ⊂ audit ⊂ admin`).
- Per-principal sliding-window rate limiting.
- CORS is explicit-origin only, never wildcard; a regex is available specifically for
  Vercel preview subdomains without loosening the production allow-list.
- Upload size is enforced on the incoming byte stream, not after full buffering.
- Text-to-SQL is structurally guardrailed (single statement, `SELECT`/`WITH` only,
  keyword blocklist applied after stripping comments/strings, table allow-list,
  `PRAGMA query_only` connection, wall-clock ceiling via SQLite's progress handler) — the
  model's good behavior is explicitly not treated as the security control.
- Secrets never enter the repo or a log line; `.env`/`providers.json` are gitignored and
  verified absent from tracked files.

What is genuinely **not** production-ready, named honestly rather than glossed over:
no encryption at rest, no tenant isolation in the SQLite schema (a global `claim_id`
primary key means two hospitals with the same claim number would collide), no SSO, no
durable job queue (an in-process pool, lost on restart), single-process rate limiting
(doesn't survive multiple replicas). All of this is `ENTERPRISE.md` §3's explicit,
ordered "what's still missing" list — nothing here was hidden or newly discovered in
this pass, it is pre-existing, accurate self-documentation.

## 11. Error / loading / empty-state handling

Verified by reading every frontend view's source, not assumed:

- Every list/detail screen (`ClaimsListView`, `ClaimDetailView`, `DashboardView`,
  `RulesCatalogView`) uses TanStack Query with real loading states (spinners) and
  explicit empty states (e.g. "No claims audited yet" on the dashboard when
  `summary.empty` is true).
- `ClaimDetailView` has its own inline "Claim not found" error state with a back button.
- File uploads show per-file failure toasts on extraction errors.
- A React error boundary wraps the whole app (`error-boundary.tsx`).
- **Fixed in this pass**: a whole-backend outage previously left every other screen's
  queries spinning indefinitely with no explanation; `ConnectionGuard` is now wired into
  `AppShell` so this now surfaces a clear, actionable message instead (see §5).
- Backend-side: every 500 response carries the request's correlation ID; validation
  errors return FastAPI/Pydantic's structured 422 body; idempotent replay of a mutating
  request returns the original response with `Idempotency-Replayed: true` rather than
  double-processing.

## 12. Deployment / readiness status

**Deployable today**, in either of two shapes:

1. **Split (recommended, documented in `DEPLOY.md`)** — FastAPI backend as a Docker
   container on Render/Fly/Railway (health-checked at `/health`, persistent disk for
   `data/`, seed-once entrypoint that never clobbers an existing database on redeploy),
   Next.js frontend on Vercel calling it directly over HTTPS. `render.yaml` and the
   `Dockerfile` were read in full during this pass and are internally consistent with
   this model — no stale Streamlit assumptions in either.
2. **Single-container demo** — `.\start.ps1` runs the API plus the bundled `web/` static
   site on one port, or `.\start.ps1 -Streamlit` additionally runs the legacy analyst UI.

`requirements-api.txt` is a correctly-trimmed subset of `requirements.txt` (drops
`streamlit`/`plotly`/dev tools, everything else pinned identically) — verified by diff,
consistent with what `Dockerfile` installs.

**One build-time note worth knowing before a cold deploy**: the frontend uses
`next/font/google` for two fonts, which fetches from `fonts.gstatic.com` at build time.
In this environment that request timed out once and succeeded on Next.js's automatic
retry — harmless here, but on a build host with restricted or flaky egress this could
make a production build fail outright rather than just retry. Vercel's build network
handles this natively, so this is unlikely to matter for the recommended deploy path,
but it is worth knowing if you ever build the frontend in a locked-down CI environment.

## 13. Testing / verification actually performed in this pass

Everything below was executed live during this session, not inferred:

| Check | Result |
|---|---|
| `python -m pytest -q` (230 tests) | **All passed**, ~15s, offline (`AI_ENABLED=false` forced by `conftest.py`) |
| `python -m ruff check claimiq/` | **All checks passed** |
| `npm run build` (frontend, twice — before and after the `ConnectionGuard` fix) | **Both succeeded** — compiled, typechecked, linted, prerendered 8 routes |
| `GET /health` against a live `uvicorn` process | `status: ok`, real Groq key present, `ai_enabled: true`, 104 corpus chunks loaded |
| `POST /api/audit` with the real cardiac sample, live LLM calls | Correct output matching the documented worked example exactly; invariant held; `verify_passed: true` |
| `GET /api/analytics/summary`, `/api/profiles`, `/api/samples` | All returned correct, non-trivial live data |
| Full browser walkthrough (`/`, `/claims`, `/claims/{id}`, `/dashboard`, `/rules`) against the live backend | Every page rendered correct data; **zero console errors** on any page |
| Live audit run through the actual UI (not just the API) | Same correct numbers, citations, and narrative rendered end-to-end in the browser |

Not exercised in this pass (pre-existing, covered by the automated suite instead of
manual re-verification): PDF/scan upload extraction through the browser, the batch-jobs
API, the ledger-verify endpoint, and the retention/erasure endpoints. These are covered
by `tests/test_extract.py`, `tests/test_enterprise.py`, all of which pass.

## 14. Exact steps to run the complete project locally

```powershell
# Backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
# edit .env: add a free Groq key (https://console.groq.com/keys), or leave AI_ENABLED=false
python scripts/index_corpus.py
python scripts/gen_samples.py 300
python scripts/gen_bill_pdf.py
python scripts/seed_db.py
.venv\Scripts\python.exe -m uvicorn claimiq.api:app --host 127.0.0.1 --port 8000
```

Confirm: `curl http://127.0.0.1:8000/health` → `"status":"ok"`.

```powershell
# Frontend, in a second terminal
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. If the sidebar shows a connection error, add
`http://localhost:3000` to `CLAIMIQ_CORS_ORIGINS` in the root `.env` and restart the API.

Run the test suite: `python -m pytest -q` (no key required).

## 15. What to demonstrate when showing this project

1. **`/` → click "Room-rent trap" sample → run it.** Gross ₹4,10,000 collapses to a
   ~₹2,27,700 settlement. Point at the hospital-write-off card: "₹7,750 of this is a
   billing-template error the hospital will lose on every claim it files, not a patient
   cost" — that's the whole product thesis in one screen.
2. **Findings table → click a deducted line item** to expand its cited rule text —
   grounding you can inspect, not just claim.
3. **`/dashboard`** — 301 real audited claims, top-10 recurring billing mistakes ranked
   by aggregate loss. "Fix the top 3 and the leak stops."
4. **`/rules`** — the "unverified snapshot" banner, stated plainly rather than hidden.
5. If time allows, `/docs` on the API (FastAPI's generated reference) to show the
   contract is real and can't drift from the code.

## 16. Known limitations (stated plainly, not newly discovered — pre-existing and honest)

- The rule corpus (104 chunks) is an unverified snapshot, never checked against a
  current IRDAI circular — stamped and surfaced everywhere, not hidden.
- All bundled data is synthetic; real hospital bills will have messier department
  labelling than the generated benchmark set (documented and measured in
  `EVALUATION.md`, including a leak the project's own author found and published).
- Vision-based scan extraction is not viable on the free LLM tier (a flat per-image
  token cost exceeds the per-minute budget); native-PDF extraction (the common case) is
  deterministic and measured at 100% row recall, so this only affects true photo/scan
  uploads.
- Insurer behavior varies; the three profiles are plausible interpretations, not any
  specific insurer's actual rulebook — hence a range, never a single confident number.
- No authentication by default; not encrypted at rest; not tenant-isolated in the
  schema. Documented extensively and explicitly not claimed to be production-ready for
  real PHI (`ENTERPRISE.md` §3).

## 17. Remaining technical debt

- **Three parallel UIs exist** (`frontend/` Next.js — primary, `web/` static — bundled
  single-container fallback, `ui/` Streamlit — legacy/comparison). All three still work
  and none is broken, but keeping three UIs in sync if the API response shape changes
  again is real ongoing cost. Recommendation: if the split (Vercel + Render) deployment
  becomes the permanent shape, retire `ui/` and consider whether `web/` is still earning
  its place once the split deploy is the default story.
- The Next.js frontend has no screens yet for Trace, Ask (NL-to-SQL), batch jobs, the
  audit ledger, or retention — all implemented and tested on the backend, none exposed
  in the primary UI. This is the most valuable next chunk of frontend work.
- `DocumentGap`/`ConsistencyFlag` TypeScript types are narrower than their backend
  Pydantic equivalents (missing a few `Finding` base fields the frontend doesn't
  currently read). Not a bug today; would need tightening if those fields are ever
  surfaced in the UI.
- SQLite is a deliberate, currently-correct choice, but is the first thing that would
  need to change for real multi-hospital, multi-writer usage (`ENTERPRISE.md` §3, Tier 1).
- In-process rate limiting and the in-process batch-job queue do not survive a restart
  or scale past one replica — both have a named Redis-based replacement path already
  scoped in `ENTERPRISE.md`, not yet built.

## 18. Final completion status

**The project is genuinely demonstrable end-to-end, today, as-is.** Every layer was
independently verified in this pass rather than trusted from documentation: the
230-test backend suite passes, `ruff` is clean, the frontend production build compiles
and typechecks with zero errors, and a live browser session exercised every one of the
5 frontend routes against a live backend with real LLM calls, producing correct,
invariant-holding numbers on the first run with zero console errors on any page.

The gaps this pass found and closed were integration/documentation gaps (a dead
component now wired in, stale docs now describing the system as it actually runs) —
not defects in the engine, the API, or the deployed screens. Nothing was rebuilt, no
dependency was changed, and no working code was replaced; the stack was already
appropriately sized for this project's scale and is not the thing that needs revisiting
before a real deployment. What would need to be revisited — auth, tenant isolation,
durable jobs, encryption at rest — is exactly, and only, the list in
`ENTERPRISE.md` §3, and it is a scaling/hardening roadmap for serving real patient data,
not a blocker to demoing or personally deploying this project as it stands.

**Ready to deploy** via `DEPLOY.md` (Render + Vercel) or as a single Docker container.
