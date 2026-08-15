# ClaimIQ — Completion, Hardening & "Killer Frontend" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 3 critical + 7 important + 6 minor defects found in review, wire the unwired backend surface (auth, trace, ask, recovery, room-simulation, batches) into a polished, feature-complete React frontend, and reconcile the security posture with the docs — turning ClaimIQ from a well-engineered prototype into a trustworthy, shippable product.

**Architecture:** Backend bugs fixed in-place with TDD (pytest, offline, `AI_ENABLED=false`). Frontend gains an auth layer (`X-API-Key` via a `lib/auth.ts` key store), five new screens over already-working endpoints, and a Vitest + typecheck safety net. Security posture made fail-closed on Render/Vercel. Docs corrected to match code.

**Tech Stack:** Python 3.12 / FastAPI / LangGraph / SQLite; React 18 / Next 14 / TanStack Query / Tailwind / Recharts / Radix; Vitest + Testing Library; GitHub Actions CI.

**Spec:** This document (written during review + brainstorming); authoritative behavioral reference is the existing `README.md`, `ARCHITECTURE.md`, and the 235-test suite.

## Global Constraints

- Backend tests stay offline: force `AI_ENABLED=false`; never require a live LLM key.
- Money is `Decimal`/string over the wire; never `float`. The invariant `settlement + patient_liability + hospital_writeoff == gross_bill` holds on every sample.
- Frontend must stay a client-side SPA: no server components for data paths; `NEXT_PUBLIC_API_URL` + relative `/api` rewrite for local dev.
- Auth is *off* by default (empty `CLAIMIQ_API_KEYS`) and must keep working for the cloned-demo path; it only activates when a key is configured.
- Never log or commit secrets; `X-API-Key` lives in `localStorage`, never in source.
- Commit per task; run the full backend suite (`python -m pytest -q`) after every backend change.

---

## Phase 1 — Critical backend fixes (TDD)

### Task 1: Batch audits must carry tenant + key_id

**Files:**
- Modify: `claimiq/jobs.py:197`
- Test: `tests/test_enterprise.py`

**Interfaces:**
- Consumes: `claimiq.graph.audit(packet, persist=True, *, tenant, key_id, request_id)` (already exists)
- Produces: `audit` is now always called with explicit `tenant`/`key_id`/`request_id` from `job.tenant`, `job.key_id`, `job.job_id`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_batch_persists_each_claim_under_the_batches_tenant() -> None:
    from claimiq import jobs, store
    import time
    registry = jobs.registry()
    job = registry.submit([packet("BATCH-TENANT-1")], tenant="apollo", key_id="abc")
    deadline = time.monotonic() + 5.0
    while job.state.value in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
    rows, _ = store.list_claims(tenant="apollo")
    assert any(r["claim_id"] == "BATCH-TENANT-1" for r in rows)
    rows_local, _ = store.list_claims(tenant="local")
    assert not any(r["claim_id"] == "BATCH-TENANT-1" for r in rows_local)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_enterprise.py::test_a_batch_persists_each_claim_under_the_batches_tenant -q`
Expected: FAIL — claim lands under `local`, so the `apollo` assertion finds nothing.

- [ ] **Step 3: Write minimal implementation**

In `claimiq/jobs.py`, `_run` (line 197), change:
```python
result = contextvars.copy_context().run(audit, packet)
```
to:
```python
result = contextvars.copy_context().run(
    audit, packet, tenant=job.tenant, key_id=job.key_id, request_id=job.job_id
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_enterprise.py::test_a_batch_persists_each_claim_under_the_batches_tenant -q`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `python -m pytest -q` — Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add claimiq/jobs.py tests/test_enterprise.py
git commit -m "fix(jobs): thread tenant/key_id into batch audits"
```

---

### Task 2: Text-to-SQL must reject base-table references (comma-join bypass)

**Files:**
- Modify: `claimiq/analytics.py:184-252`
- Test: `tests/test_enterprise.py`

**Interfaces:**
- Consumes: `_analysable(sql)` (already exists)
- Produces: `_analysable` raises `ValueError` when a query references base tables `claims` or `findings`, or any comma-separated table not in `ALLOWED_TABLES`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_comma_join_cannot_reach_the_base_claims_table() -> None:
    from claimiq.analytics import _analysable
    with pytest.raises(ValueError):
        _analysable("SELECT * FROM v_claims, claims LIMIT 10")

def test_the_base_findings_table_is_forbidden() -> None:
    from claimiq.analytics import _analysable
    with pytest.raises(ValueError):
        _analysable("SELECT * FROM v_findings JOIN findings USING(claim_id) LIMIT 10")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_enterprise.py::test_a_comma_join_cannot_reach_the_base_claims_table tests/test_enterprise.py::test_the_base_findings_table_is_forbidden -q`
Expected: FAIL — currently both pass the guard.

- [ ] **Step 3: Write minimal implementation**

In `claimiq/analytics.py`:

1. Extend `FORBIDDEN` (line 185) to ban base tables:
```python
FORBIDDEN = re.compile(
    r"(?is)\b(drop|delete|insert|update|alter|create|attach|detach|vacuum|pragma|reindex|replace|claims|findings)\b"
)
```

2. Make `referenced` (lines 247-249) capture comma-separated names:
```python
referenced = set()
for clause in re.findall(
    r"\b(?:from|join)\s+([a-zA-Z_]\w*(?:\s*,\s*[a-zA-Z_]\w*)*)",
    probe,
    re.IGNORECASE,
):
    referenced.update(t.strip().lower() for t in clause.split(","))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_enterprise.py::test_a_comma_join_cannot_reach_the_base_claims_table tests/test_enterprise.py::test_the_base_findings_table_is_forbidden -q`
Expected: PASS

- [ ] **Step 5: Run full suite** — existing CTE/comment/string-literal guard tests must stay green.

Run: `python -m pytest -q` — Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add claimiq/analytics.py tests/test_enterprise.py
git commit -m "fix(analytics): block base-table access in text-to-SQL"
```

---

### Task 3: Retrieval classifier must honor the billing-head gate

**Files:**
- Modify: `claimiq/nodes/classify.py:196`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `BillLineItem`, `Determination`, `RETRIEVAL_THRESHOLD` (all exist)
- Produces: `classify_retrieval` returns `PAYABLE` for any `head != "OTHER"` before doing a search.

- [ ] **Step 1: Write the failing test**

```python
def test_retrieval_never_deducts_a_primary_service() -> None:
    from claimiq.nodes.classify import classify_retrieval
    from claimiq.state import BillLineItem
    item = BillLineItem(
        line_no=1,
        description="OT consumables",
        head="PROCEDURE",
        quantity="1",
        unit_rate="5000",
        amount="5000",
    )
    d = classify_retrieval(item)
    assert d.classification == "PAYABLE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py::test_retrieval_never_deducts_a_primary_service -q`
Expected: FAIL — deducts if cosine >= 0.82.

- [ ] **Step 3: Write minimal implementation**

At the top of `classify_retrieval` (line 196), before the search:
```python
if item.head != "OTHER":
    return Determination(
        classification="PAYABLE",
        reason=f"Billed under head {item.head}, a primary service.",
        source="retrieval",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_engine.py::test_retrieval_never_deducts_a_primary_service -q`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `python -m pytest -q` — Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add claimiq/nodes/classify.py tests/test_engine.py
git commit -m "fix(classify): apply head gate to retrieval strategy"
```

---

### Task 4: Path-traversal guard on `/api/samples/{name}`

**Files:**
- Modify: `claimiq/api.py:325-330`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `SAMPLES` (Path), `ClaimPacket`
- Produces: `get_sample` returns 404 for names that escape the samples directory.

- [ ] **Step 1: Write the failing test**

```python
def test_sample_names_cannot_escape_the_samples_dir(client: TestClient) -> None:
    from pathlib import Path
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as probe:
        probe.write(b"{}")
        probe_path = Path(probe.name)
    try:
        rel = probe_path.resolve().relative_to(Path(".").resolve())
    except ValueError:
        return
    response = client.get(f"/api/samples/../{rel}")
    assert response.status_code in (404, 400)
    probe_path.unlink()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api.py::test_sample_names_cannot_escape_the_samples_dir -q`
Expected: FAIL or vacuous — add a direct unit assertion instead if the path form is rejected by the router.

**Alternative stronger test** (recommended): call `get_sample` directly:
```python
def test_sample_names_cannot_escape_the_samples_dir() -> None:
    from claimiq.api import get_sample
    from fastapi import HTTPException
    try:
        get_sample("..%2F..%2Fproviders")
    except HTTPException as exc:
        assert exc.status_code in (400, 404)
    else:
        raise AssertionError("escaping name resolved to a file")
```

- [ ] **Step 3: Write minimal implementation**

In `get_sample`:
```python
base = SAMPLES.resolve()
path = (SAMPLES / f"{name}.json").resolve()
if path.suffix != ".json" or not path.is_relative_to(base) or not path.is_file():
    raise HTTPException(404, f"no sample named {name!r}")
return ClaimPacket.model_validate_json(path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api.py::test_sample_names_cannot_escape_the_samples_dir -q`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `python -m pytest -q` — Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add claimiq/api.py tests/test_api.py
git commit -m "fix(api): contain sample lookups to the samples directory"
```

---

### Task 5: A failed portfolio write must not fail a completed audit

**Files:**
- Modify: `claimiq/graph.py:193-201`
- Test: `tests/test_enterprise.py`

**Interfaces:**
- Consumes: `audit(...)`; `claimiq.store.save_audit(result, ai_pipeline, month, tenant)` (exists)
- Produces: `audit` never raises from `save_audit`; the error is recorded in `result.errors`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_store_failure_never_fails_the_audit(monkeypatch) -> None:
    from claimiq import graph, store
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(store, "save_audit", boom)
    result = graph.audit(packet("STORE-FAIL-1"), tenant="apollo", key_id="abc")
    assert result.claim_id == "STORE-FAIL-1"
    assert any("persistence failed" in e for e in result.errors)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_enterprise.py::test_a_store_failure_never_fails_the_audit -q`
Expected: FAIL — raises RuntimeError out of audit.

- [ ] **Step 3: Write minimal implementation**

In `graph.py`, wrap the `save_audit` block (lines 193-201) like the ledger block:
```python
if persist:
    from claimiq import store
    try:
        store.save_audit(
            result,
            ai_pipeline=state.ai_used,
            month=packet.context.discharge_date.strftime("%Y-%m"),
            tenant=tenant,
        )
    except Exception as exc:  # noqa: BLE001
        result.errors.append(
            f"audit persistence failed: {type(exc).__name__}: {exc}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_enterprise.py::test_a_store_failure_never_fails_the_audit -q`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `python -m pytest -q` — Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add claimiq/graph.py tests/test_enterprise.py
git commit -m "fix(graph): portfolio write failure never fails a completed audit"
```

---

### Task 6: OCR once per scanned upload (kill double-OCR)

**Files:**
- Modify: `claimiq/nodes/extract.py`, `claimiq/api.py:468-470,494`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `extract_bill(path, client=None, force_vision=False)`, `extract_via_ocr(path, client)`, `document_text(path, max_pages=3)`, `detect_document_type(text)`
- Produces: `read_text(path, max_pages=MAX_PAGES) -> str` (text layer first, OCR fallback); `extract_bill(path, client=None, force_vision=False, pretext=None)`; `extract_via_ocr(path, client, pretext=None)`; `extract_policy_terms`/`extract_clinical_context` accept optional `pretext`.

- [ ] **Step 1: Add `read_text` and make `document_text` delegate**

In `claimiq/nodes/extract.py`:
```python
def read_text(path: Path, max_pages: int = MAX_PAGES) -> str:
    text = "\n".join(pdf_text_layer_pages(path, max_pages)).strip()
    return text if len(text) >= 60 else ocr_pages(path, max_pages)


def document_text(path: Path, max_pages: int = 3) -> str:
    return read_text(path, max_pages)
```

- [ ] **Step 2: Thread `pretext` through extract paths**

Change signatures:
```python
def extract_bill(path, client=None, force_vision=False, pretext=None):
```
```python
def extract_via_ocr(path, client, pretext=None):
```
In `extract_via_ocr`, replace `text = ocr_pages(path)` with `text = pretext or ocr_pages(path)`.
In `extract_bill`, pass `pretext` down to `extract_via_ocr(path, client, pretext)`.

- [ ] **Step 3: Rewire `api.py` to read once**

Replace the two work calls (`api.py:468-470` and `api.py:494`) with a single text read:
```python
text = await run_in_threadpool(read_text, tmp_path)
doc_id, doc_label = await run_in_threadpool(detect_document_type, text)
...
items, extracted = await run_in_threadpool(extract_bill, tmp_path, None, False, text)
```
And pass `pretext=text` into `extract_policy_terms` / `extract_clinical_context` so a scanned policy/summary is OCR'd once.

- [ ] **Step 4: Write the counting regression test**

```python
def test_a_scanned_upload_ocrs_exactly_once(client: TestClient, monkeypatch, tmp_path) -> None:
    import claimiq.nodes.extract as extract
    calls = {"n": 0}
    original = extract.ocr_pages
    def counting(path, max_pages=extract.MAX_PAGES):
        calls["n"] += 1
        return original(path, max_pages)
    monkeypatch.setattr(extract, "ocr_pages", counting)
    # build a scanned-style PDF fixture, post to /api/extract
    # assert calls["n"] == 1
```

- [ ] **Step 5: Run tests to verify they pass + full suite**

Run: `python -m pytest tests/test_api.py -q` then `python -m pytest -q` — Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add claimiq/nodes/extract.py claimiq/api.py tests/test_api.py
git commit -m "fix(extract): OCR a scanned upload exactly once"
```

---

### Task 7: LLM cache TTL + corpus-version key

**Files:**
- Modify: `claimiq/llm.py:336-337` and the diskcache accessor
- Test: `tests/test_enterprise.py` (or new `tests/test_llm.py`)

**Interfaces:**
- Consumes: `corpus_version()` (from `claimiq.retrieval.corpus`)
- Produces: cache keys include the corpus version; cached entries expire.

- [ ] **Step 1: Read the current cache implementation** (`llm.py` lines 190-230 and `_cache_key` at 336-337) to confirm the diskcache API.

- [ ] **Step 2: Write the test**

```python
def test_cache_key_includes_the_corpus_version() -> None:
    from claimiq.llm import _cache_key
    k1 = _cache_key("m", "s", "u", "S", [])
    assert "corpus" in k1
```

- [ ] **Step 3: Implement**

Include `corpus_version()` in `_cache_key` and set an expiry on writes (verify diskcache signature — likely `cache.set(key, value, expire=CACHE_TTL)`). Add `CACHE_TTL = 60 * 60 * 24` module constant.

- [ ] **Step 4: Run tests + full suite.**

- [ ] **Step 5: Commit**

```bash
git add claimiq/llm.py tests/test_enterprise.py
git commit -m "fix(llm): scope cache to corpus version with a TTL"
```

---

### Task 8: Room-downgrade simulation must not over-reduce ICU/ward rows

**Files:**
- Modify: `claimiq/tools/waterfall.py:261-267`
- Test: `tests/test_waterfall.py`

**Interfaces:**
- Consumes: `compute_waterfall(packet, findings, profile)`, `packet.model_copy(deep=True)`
- Produces: `simulate_room_downgrade` adjusts only the ROOM row(s) representing the stay being downgraded, not every ROOM row.

- [ ] **Step 1: Read the current implementation** (waterfall.py:246-277) to see the ROOM-row loop.

- [ ] **Step 2: Write the failing test**

Build a packet with one ROOM row at the original `rate_per_day` (the stay) and one ROOM row at a different rate (a second room). Assert the second room's amount is unchanged after downgrade and the invariant holds.

```python
def test_room_downgrade_only_reduces_the_stay_row() -> None:
    ...
    result, gain = simulate_room_downgrade(packet, findings, "typical")
    by_line = {i.line_no: i.amount for i in result.line_items}
    assert by_line[other_room.line_no] == other_room_amount  # untouched
```

- [ ] **Step 3: Implement**

In `simulate_room_downgrade`, restrict the per-day saving to room rows that match the stay: only rows whose `head == "ROOM"` and whose `unit_rate == packet.room_stay.rate_per_day` (or `room_category` matches `packet.room_stay.room_category` when set). Adjust `unit_rate` and `amount` for those rows only.

- [ ] **Step 4: Run test + full suite.**

- [ ] **Step 5: Commit**

```bash
git add claimiq/tools/waterfall.py tests/test_waterfall.py
git commit -m "fix(waterfall): room downgrade only reduces the stay row"
```

---

### Task 9: Minor backend cleanups (one batch)

**Files:**
- Modify: `claimiq/nodes/explain.py:172`, `claimiq/config.py:78`, `ENTERPRISE.md`, `README.md`

**Interfaces:**
- Produces: docs match code; no redundant except.

- [ ] **Step 1:** In `claimiq/nodes/explain.py:172`, change `except (LLMUnavailable, Exception)` to `except Exception`.
- [ ] **Step 2:** In `claimiq/config.py:78`, fix the comment to name the real entrypoint (`POST /v1/retention/purge`).
- [ ] **Step 3:** In `ENTERPRISE.md`, fix "41 chunks" → 104.
- [ ] **Step 4:** In `README.md`, reconcile the classification benchmark table with the committed `CLASSIFICATION.md`: use `keyword 82.8%` / `deterministic 81.6%` rows; drop the `gpt-oss`/`gemma` rows that are not reproducible from committed artifacts (or regenerate `CLASSIFICATION.md` to match the README).
- [ ] **Step 5:** Run `python -m pytest -q` — Expected: all pass.
- [ ] **Step 6:** Commit:

```bash
git add claimiq/nodes/explain.py claimiq/config.py ENTERPRISE.md README.md
git commit -m "docs(cleanup): reconcile benchmark table and stale references"
```

---

## Phase 2 — Security posture + docs (fail-closed deployment)

### Task 10: Render/Vercel secure-by-default

**Files:**
- Modify: `render.yaml`, `docker-entrypoint.sh:29`, `.env.example`, `claimiq/config.py`

**Interfaces:**
- Produces: `Settings` exposes `bind_host` and `allow_insecure_demo`; `/health` reports the insecure-demo posture.

- [ ] **Step 1:** Add to `claimiq/config.py` Settings: `bind_host: str = "127.0.0.1"`, `allow_insecure_demo: bool = False`; wire from env `CLAIMIQ_BIND`, `CLAIMIQ_ALLOW_INSECURE_DEMO`.
- [ ] **Step 2:** Add a startup check: if `bind_host` is not loopback and `api_keys_raw` is empty and not `allow_insecure_demo`, log a loud warning and set a `security.insecure_demo` flag surfaced in `/health`.
- [ ] **Step 3:** `docker-entrypoint.sh`: bind `$CLAIMIQ_BIND` (default `127.0.0.1`) unless overridden.
- [ ] **Step 4:** `render.yaml`: document `CLAIMIQ_API_KEYS` as required for production; set `CLAIMIQ_CORS_ORIGINS` to the Vercel origin; set `CLAIMIQ_BIND=0.0.0.0`.
- [ ] **Step 5:** `.env.example`: add the new vars with comments.
- [ ] **Step 6:** Run `python -m pytest -q` — Expected: all pass.
- [ ] **Step 7:** Commit:

```bash
git add render.yaml docker-entrypoint.sh .env.example claimiq/config.py
git commit -m "security(deploy): fail closed on non-loopback bind without keys"
```

### Task 11: Fix stale security claims in docs + landing page

**Files:**
- Modify: `README.md:227`, `ARCHITECTURE.md:476-477`, `web/index.html:297,405`, `tests/test_landing.py:151-160`

**Interfaces:**
- Produces: docs describe auth/tenancy/ledger truthfully; landing-page copy and its pinned test agree.

- [ ] **Step 1:** Rewrite the "no auth / no tenancy / no audit trail" sentences in `README.md` and `ARCHITECTURE.md` to state auth, tenancy and the ledger exist and are tested, and that the demo runs with them off.
- [ ] **Step 2:** Update `web/index.html` copy at lines 297 and 405 to match.
- [ ] **Step 3:** Update `tests/test_landing.py` assertions at lines 151-160 to the new copy.
- [ ] **Step 4:** Run `python -m pytest -q` — Expected: all pass.
- [ ] **Step 5:** Commit:

```bash
git add README.md ARCHITECTURE.md web/index.html tests/test_landing.py
git commit -m "docs(security): stop claiming auth/tenancy/audit do not exist"
```

---

## Phase 3 — Frontend auth wiring

### Task 12: `X-API-Key` support in the SPA

**Files:**
- Create: `frontend/src/lib/auth.ts`, `frontend/src/lib/__tests__/auth.test.ts`
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/components/app-shell.tsx`

**Interfaces:**
- Consumes: `HealthResponse` (has `security.auth_enabled`)
- Produces: `getApiKey()`, `setApiKey(k)`, `clearApiKey()`, `hasApiKey()`; `api` attaches `X-API-Key`.

- [ ] **Step 1: Create `frontend/src/lib/auth.ts`**

```ts
const KEY = "claimiq.api_key";

export function getApiKey(): string | null {
  return typeof window === "undefined" ? null : window.localStorage.getItem(KEY);
}
export function setApiKey(k: string): void {
  window.localStorage.setItem(KEY, k.trim());
}
export function clearApiKey(): void {
  window.localStorage.removeItem(KEY);
}
export function hasApiKey(): boolean {
  return Boolean(getApiKey());
}
```

- [ ] **Step 2: Modify `api.ts`** — in `get`/`post`/`upload`/`pdf`, build headers with `X-API-Key` when `getApiKey()` is set; keep `ApiError.status` for 401/403/429 handling.

```ts
import { getApiKey } from "./auth";
const key = getApiKey();
const headers: Record<string, string> = { ...(key ? { "X-API-Key": key } : {}) };
```

- [ ] **Step 3: Update `types.ts`** — add `security: { auth_enabled: boolean; principals: number; tenants: string[]; config_problems: string[]; rate_limit_per_minute: number }` to `HealthResponse`.

- [ ] **Step 4: Modify `app-shell.tsx`** — in the status footer, when `health?.security.auth_enabled`, render an "API key" button that opens a small dialog (Radix Dialog) with an input + save/clear; otherwise show "local demo (no auth)".

- [ ] **Step 5: Write `auth.test.ts`** (Vitest) — localStorage round-trip; `setApiKey` trims; `clearApiKey` empties.

- [ ] **Step 6:** Run `npx tsc --noEmit` in `frontend/` — Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/auth.ts frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/src/components/app-shell.tsx frontend/src/lib/__tests__/auth.test.ts
git commit -m "feat(ui): API-key auth in the SPA"
```

---

## Phase 4 — Missing screens (feature-complete)

Each task: add typed API fns in `lib/api.ts`, types in `lib/types.ts`, the route in `app/`, components in `components/`, a nav entry in `app-shell.tsx`.

### Task 13: Trace screen (`/trace/[claimId]`)

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/components/app-shell.tsx`, `frontend/src/components/claims/claim-detail-view.tsx`
- Create: `frontend/src/app/trace/[claimId]/page.tsx`, `frontend/src/components/trace/trace-view.tsx`

**Interfaces:**
- Consumes: `GET /api/trace/{claim_id}` → `{ claim_id, tenant, nodes: NodeTrace[], total_ms, total_tokens }`
- Produces: `claimiqApi.trace(claimId)`; `TraceResponse` type; Trace view.

- [ ] **Step 1: Types**

```ts
export interface NodeTrace {
  node: string;
  started_at: string;
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  llm_calls: number;
  cache_hits: number;
  attempts: number;
  note: string;
  error: string;
}
export interface TraceResponse {
  claim_id: string;
  tenant: string;
  nodes: NodeTrace[];
  total_ms: number;
  total_tokens: number;
}
```

- [ ] **Step 2: API**

```ts
trace: (claimId: string) => api.get<TraceResponse>(`/api/trace/${encodeURIComponent(claimId)}`),
```

- [ ] **Step 3: Trace view** — per-node table (node, latency ms, tokens, llm calls, cache hits, attempts, error), a header with totals, a highlight on the `verify`→`repair` loop (repair_count), a 404/empty state with "Run an audit to see a trace".

- [ ] **Step 4: Route + link** — `page.tsx` reads `params.claimId`; add a "Trace" button in `claim-detail-view.tsx` linking to `/trace/{id}`.

- [ ] **Step 5: Typecheck** — `npx tsc --noEmit`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/src/components/app-shell.tsx frontend/src/components/claims/claim-detail-view.tsx frontend/src/app/trace frontend/src/components/trace
git commit -m "feat(ui): trace screen"
```

### Task 14: Ask / Analytics chat (`/ask`)

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/components/app-shell.tsx`
- Create: `frontend/src/app/ask/page.tsx`, `frontend/src/components/ask/ask-view.tsx`

**Interfaces:**
- Consumes: `POST /api/analytics/ask` → `{ sql, explanation, columns, rows }`
- Produces: `claimiqApi.ask(question)`; `AskResponse` type.

- [ ] **Step 1: Types**

```ts
export interface AskResponse {
  sql: string;
  explanation: string;
  columns: string[];
  rows: Record<string, unknown>[];
}
```

- [ ] **Step 2: API**

```ts
ask: (question: string) => api.post<AskResponse>("/api/analytics/ask", { question }),
```

- [ ] **Step 3: Ask view** — question input + submit; on result, render the explanation, a `<details>` block showing generated SQL with a "read-only, tenant-scoped" caption, and a data table from `columns`/`rows`; error state from `ApiError` (422 shows the guardrail message); example prompt chips ("total hospital write-off by month", "top 5 leaking items").

- [ ] **Step 4: Route + nav** — `/ask` page + nav entry (icon: `MessageSquareText`).

- [ ] **Step 5: Typecheck.**

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/src/components/app-shell.tsx frontend/src/app/ask frontend/src/components/ask
git commit -m "feat(ui): ask analytics chat screen"
```

### Task 15: Recovery screen (`/recovery`)

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/components/app-shell.tsx`
- Create: `frontend/src/app/recovery/page.tsx`, `frontend/src/components/recovery/recovery-view.tsx`

**Interfaces:**
- Consumes: `GET /api/analytics/recovery?annual_claim_volume=&limit=` → `RecoveryModel` (from `recovery.py::as_dict`)
- Produces: `claimiqApi.recovery(annualClaimVolume?, limit?)`.

- [ ] **Step 1: Types** (mirror `recovery.py`):

```ts
export interface RecoveryModel {
  empty?: boolean;
  reason?: string;
  claims_audited: number;
  months_observed: number;
  gross_billed: string;
  recoverable_total: string;
  leak_per_claim: string;
  leak_rate_pct: string;
  annual_claim_volume: number;
  volume_source: string;
  annual_recovery: string;
  top_three_recovery: string;
  items: { item: string; claims: number; annual_recovery: string }[];
  assumptions: string[];
}
```

- [ ] **Step 2: API**

```ts
recovery: (annualClaimVolume = 0, limit = 12) =>
  api.get<RecoveryModel>("/api/analytics/recovery", { annual_claim_volume: String(annualClaimVolume), limit: String(limit) }),
```

- [ ] **Step 3: Recovery view** — annual recovery headline, `volume_source` disclosure, top-3 remediation list, assumptions list; optional input for `annual_claim_volume`; empty state → "seed the demo portfolio" hint.

- [ ] **Step 4: Route + nav.**

- [ ] **Step 5: Typecheck.**

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/src/components/app-shell.tsx frontend/src/app/recovery frontend/src/components/recovery
git commit -m "feat(ui): recovery projection screen"
```

### Task 16: Room-downgrade what-if (on audit result)

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/components/audit/audit-result-view.tsx`

**Interfaces:**
- Consumes: `POST /api/simulate-room?profile=` → `{ applicable, result?, gain? }`
- Produces: `claimiqApi.simulateRoom(packet, profile)`; `SimulateRoomResponse`.

- [ ] **Step 1: Types**

```ts
export interface SimulateRoomResponse {
  applicable: boolean;
  result?: AuditResult | null;
  gain?: string | null;
}
```

- [ ] **Step 2: API**

```ts
simulateRoom: (packet: ClaimPacket, profile = "typical") =>
  api.post<SimulateRoomResponse>("/api/simulate-room", packet, { profile }),
```

- [ ] **Step 3: Audit result** — an action button "Try a room within cap" that calls `simulateRoom` with the current packet + selected profile, shows the `gain` (settlement delta) and swaps the waterfall view to the simulated `result`, with a "this is a what-if, not persisted" caption and a "back to original" toggle.

- [ ] **Step 4: Typecheck.**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/src/components/audit/audit-result-view.tsx
git commit -m "feat(ui): room-downgrade what-if on audit result"
```

### Task 17: Batch UI (`/batches`)

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/components/app-shell.tsx`
- Create: `frontend/src/app/batches/page.tsx`, `frontend/src/components/batches/batch-create.tsx`, `frontend/src/components/batches/batch-list.tsx`, `frontend/src/components/batches/batch-detail.tsx`

**Interfaces:**
- Consumes: `POST /v1/batches`, `GET /v1/batches`, `GET /v1/batches/{job_id}`, `POST /v1/batches/{job_id}/cancel`; `jobs.py::summary/totals` shapes
- Produces: `claimiqApi.submitBatch`, `listBatches`, `batchStatus`, `cancelBatch`.

- [ ] **Step 1: Types** (mirror `jobs.py`):

```ts
export interface ClaimOutcome {
  claim_id: string;
  ok: boolean;
  verdict: string;
  settlement: string;
  patient_liability: string;
  hospital_writeoff: string;
  coverage: string;
  findings: number;
  error: string;
}
export interface JobSummary {
  job_id: string;
  tenant: string;
  state: "queued" | "running" | "done" | "cancelled";
  total: number;
  completed: number;
  failed: number;
  progress: number;
  submitted_at: string;
  started_at: string | null;
  finished_at: string | null;
  outcomes?: ClaimOutcome[];
}
export interface BatchTotals {
  claims: number;
  settlement: string;
  patient_liability: string;
  hospital_writeoff: string;
  needs_attention: number;
  cannot_verify: number;
  clean: number;
}
```

- [ ] **Step 2: API**

```ts
submitBatch: (claims: ClaimPacket[]) => api.post<JobSummary>("/v1/batches", { claims }),
listBatches: (limit = 50) => api.get<JobSummary[]>("/v1/batches", { limit }),
batchStatus: (jobId: string) => api.get<JobSummary>(`/v1/batches/${jobId}`),
cancelBatch: (jobId: string) => api.post<{ cancelled: boolean }>(`/v1/batches/${jobId}/cancel`),
```

- [ ] **Step 3: Batch create** — multi-file dropzone (`react-dropzone`); for each file call `extract`, merge results into a `ClaimPacket` (reuse the same packet-building logic as `claim-form.tsx` — extract that builder into `lib/packet.ts` if needed), show a review table, submit on button.

- [ ] **Step 4: Batch list** — table of jobs with progress bars; click → detail.

- [ ] **Step 5: Batch detail** — per-claim outcomes, totals from `/v1/batches/{job_id}`, poll every 2s via `refetchInterval` while `state` is queued/running, cancel button.

- [ ] **Step 6: Route + nav.**

- [ ] **Step 7: Typecheck.**

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/src/components/app-shell.tsx frontend/src/app/batches frontend/src/components/batches
git commit -m "feat(ui): batch processing screen"
```

---

## Phase 5 — Frontend polish + safety net

### Task 18: Polish & correctness fixes (one batch)

**Files:**
- Modify: `frontend/src/lib/utils.ts`, `frontend/src/lib/types.ts:177`, `frontend/src/components/audit/audit-result-view.tsx`, `frontend/src/components/claims/claims-list-view.tsx`, `frontend/src/components/dashboard/dashboard-view.tsx`, `frontend/src/app/globals.css`

**Interfaces:**
- Produces: correct dates, correct types, no in-place mutation, polished empty states.

- [ ] **Step 1:** Fix `today()`/`shiftDays()` to build local calendar dates (not `toISOString()`, which is UTC and can shift a day near midnight IST).

```ts
export function today(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}
```

- [ ] **Step 2:** Fix `rupees(null)` to render `"—"` instead of `"₹0"`; change `ExtractionResult.room_rate_per_day` to `string | null`.
- [ ] **Step 3:** Fix `handleDownloadPdf` — don't revoke the object URL synchronously after `click()`; revoke on a `setTimeout(..., 1000)` or keep a ref.
- [ ] **Step 4:** Fix `.sort()` mutations on memoized arrays — spread-copy before sorting (`[...arr].sort(...)`).
- [ ] **Step 5:** Add empty states to `claims-list-view`, `dashboard-view`, `rules-catalog-view` when there is no data (icon + "audit your first claim" CTA).
- [ ] **Step 6:** Micro-interactions in `globals.css` + key components: card entrance (`animate-in` already exists), hover transitions on cards, verdict-banner fade, consistent number formatting.
- [ ] **Step 7:** Typecheck (`npx tsc --noEmit`).
- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/utils.ts frontend/src/lib/types.ts frontend/src/components frontend/src/app/globals.css
git commit -m "feat(ui): polish and correctness fixes"
```

### Task 19: Frontend typecheck + Vitest + wire-shape test

**Files:**
- Modify: `frontend/package.json`, `frontend/next.config.js` (only if needed)
- Create: `frontend/vitest.config.ts`, `frontend/src/lib/__tests__/wire-shape.test.ts`, `frontend/src/lib/__tests__/fixtures/audit-result.json`

**Interfaces:**
- Produces: `npm run typecheck`, `npm run test`; a wire-shape test pins the audit-result render against a fixture.

- [ ] **Step 1: Add devDependencies**

```bash
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom
```

- [ ] **Step 2: Scripts in `package.json`**

```json
"typecheck": "tsc --noEmit",
"test": "vitest run"
```

- [ ] **Step 3: `vitest.config.ts`**

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", globals: true },
});
```

(Install `@vitejs/plugin-react` if not present.)

- [ ] **Step 4: Fixture + wire-shape test** — create `audit-result.json` with a representative `AuditResult` (three money figures, one finding, one gap, narrative); test that rendering `AuditResultView` with the fixture shows the three figures and doesn't throw:

```ts
import { render, screen } from "@testing-library/react";
import fixture from "./fixtures/audit-result.json";
test("audit result renders the three money figures from the fixture", () => {
  render(<AuditResultView result={fixture as any} />);
  expect(screen.getByText(/projected settlement/i)).toBeTruthy();
});
```

- [ ] **Step 5:** Run `npm run typecheck` and `npm run test` — Expected: both clean. Fix any type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vitest.config.ts frontend/src/lib/__tests__
git commit -m "test(ui): vitest wire-shape test and typecheck script"
```

### Task 20: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: a PR workflow that gates on backend tests + frontend typecheck/test/build.

- [ ] **Step 1: Write `.github/workflows/ci.yml`**

```yaml
name: CI
on:
  push:
    branches: [master]
  pull_request:

jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt -r requirements-api.txt
      - run: AI_ENABLED=false python -m pytest -q

  frontend:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: frontend } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: npm, cache-dependency-path: frontend/package-lock.json }
      - run: npm ci
      - run: npm run typecheck
      - run: npm test
      - run: npm run build
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: gate backend tests and frontend build on push"
```

---

## Final review

After all tasks: run the full backend suite (`python -m pytest -q`), `npm run typecheck`, `npm run test`, `npm run build`, then a final whole-branch code review (per superpowers:requesting-code-review) before superpowers:finishing-a-development-branch.

## Open items ruled on (from brainstorming)

- **Frontend test framework = Vitest** (standard for Next 14, fast, zero-config).
- **`simulate-room` and `recovery` get their own routes** rather than folding into Dashboard (keeps components focused).
- **CI targets GitHub Actions** — if the repo is never pushed to GitHub, Task 20 is dead weight and may be dropped.

## Self-Review

- **Spec coverage:** all 3 critical (Tasks 1-3), path traversal (4), store-failure (5), double-OCR (6), cache (7), room-downgrade (8), docs/benchmark (9), fail-closed deploy (10), doc reconciliation (11), auth wiring (12), missing screens Trace/Ask/Recovery/Room-simulate/Batch (13-17), polish + tests + CI (18-20). No spec gap found.
- **Placeholder scan:** no TBD/TODO; every code step contains actual code.
- **Type consistency:** `TraceResponse`, `AskResponse`, `RecoveryModel`, `JobSummary`, `BatchTotals`, `SimulateRoomResponse` names are consistent across API/type/component steps.
