"""FastAPI surface.

The Streamlit UI talks to this over HTTP rather than importing the engine, so the
API is a real boundary and not decoration.

Two route families, and the split is a promise rather than tidying:

  /api/*  the surface the bundled UI calls. Unversioned, because the UI ships in this
          repo and the two change together.
  /v1/*   the surface an integrator gets -- batch, ledger, provenance. Versioned
          because somebody else's code depends on it, which is the entire difference.

Cross-cutting concerns (correlation id, authentication, rate limit, metrics, access
log) are applied by middleware to both, rather than repeated per route. A security
control that must be remembered on every new endpoint is one that will eventually be
forgotten on one.
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import APIError
from pydantic import BaseModel, Field

from claimiq import (
    analytics,
    idempotency,
    jobs,
    ledger,
    observability,
    providers,
    recovery,
    retention,
    store,
    tenancy,
    trace,
)
from claimiq.config import ROOT, settings
from claimiq.graph import audit
from claimiq.llm import LLMUnavailable, try_client
from claimiq.nodes.extract import (
    ExtractedBill,
    detect_document_type,
    document_text,
    extract_bill,
    extract_clinical_context,
    extract_policy_terms,
    room_stay_from_items,
)
from claimiq.observability import METRICS, log
from claimiq.report import build_report
from claimiq.retrieval.corpus import corpus_version, load_corpus, sources, stale_sources
from claimiq.retrieval.search import get_index
from claimiq.state import AuditResult, ClaimPacket
from claimiq.tools.waterfall import PROFILES, simulate_room_downgrade

SAMPLES = ROOT / "data" / "samples"
WEB = ROOT / "web"

app = FastAPI(title="ClaimIQ", version="1.1.0")

observability.configure_logging(settings().json_logs)

# Same-origin by default: the SPA is served from this very app, so no CORS header is
# the correct answer and a wildcard would be a gift to anyone hosting a page that
# wants a hospital's claim data. Configured explicitly when someone genuinely embeds
# the API from another origin -- e.g. the React frontend deployed separately on
# Vercel. `allow_origin_regex` covers Vercel's per-branch preview subdomains without
# needing an env var edit on every PR; the exact list still names the real production
# domain, so the regex is additive rather than a replacement for it.
if settings().cors_origins or settings().cors_origin_regex:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings().cors_origins,
        allow_origin_regex=settings().cors_origin_regex or None,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )


# --- cross-cutting middleware ---------------------------------------------


# Reachable without a key even when authentication is on. `/health` and `/metrics` are
# what a load balancer and a scraper poll, and putting a credential on a liveness probe
# is how a deployment ends up with the same key in five unrelated config files.
# `/health` is already scrubbed of anything sensitive; `/metrics` carries counts only.
PUBLIC_PATHS = {"/health", "/metrics", "/docs", "/redoc", "/openapi.json"}


def _is_public(path: str) -> bool:
    # The SPA and its assets are public in the same sense a login page is: the bundle
    # is not the data, and gating it means the browser cannot render the screen that
    # would collect the credential.
    return path in PUBLIC_PATHS or not path.startswith(("/api/", "/v1/"))


@app.middleware("http")
async def correlate_and_meter(request: Request, call_next):
    """One id per request, one metric per request, one log line per request.

    Runs before authentication so that a rejected request is still logged and still
    counted. An auth failure that leaves no trace is precisely the one worth seeing.
    """
    rid = request.headers.get("X-Request-ID") or observability.new_request_id()
    observability.request_id.set(rid)
    observability.principal_label.set("-")

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        METRICS.inc("claimiq_requests_total", {"path": _route(request), "status": "500"})
        raise

    elapsed = time.perf_counter() - started
    route = _route(request)
    METRICS.inc("claimiq_requests_total", {"path": route, "status": str(response.status_code)})
    METRICS.observe("claimiq_request_duration_seconds", elapsed, {"path": route})

    response.headers["X-Request-ID"] = rid
    if not _is_public(request.url.path):
        log(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=int(elapsed * 1000),
        )
    return response


def _route(request: Request) -> str:
    """The route template, not the concrete path.

    `/api/claims/{claim_id}` rather than `/api/claims/SYNTH-0042`. Labelling metrics
    with the concrete path gives one time series per claim, which is the classic way
    to make a metrics backend fall over -- and it puts claim identifiers into a store
    that is usually far less protected than the database they came from.
    """
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


@app.middleware("http")
async def authenticate(request: Request, call_next):
    """Resolve the caller, or reject. No-op when no keys are configured."""
    if _is_public(request.url.path):
        return await call_next(request)

    presented = request.headers.get("X-API-Key") or _bearer(request)
    principal = tenancy.resolve(presented)
    if principal is None:
        METRICS.inc("claimiq_auth_failures_total")
        log("auth.rejected", path=request.url.path, had_key=bool(presented))
        return JSONResponse(
            status_code=401,
            content={"detail": "a valid API key is required (X-API-Key or Authorization: Bearer)"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    observability.principal_label.set(principal.label)
    request.state.principal = principal

    allowed, remaining, retry_after = tenancy.limiter().check(principal.label)
    if not allowed:
        METRICS.inc("claimiq_rate_limited_total", {"tenant": principal.tenant})
        return JSONResponse(
            status_code=429,
            content={"detail": f"rate limit of {settings().rate_limit_per_minute}/min exceeded"},
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    response = await call_next(request)
    if remaining >= 0:
        response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


def _bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else None


def caller(request: Request) -> tenancy.Principal:
    """The resolved principal. Anonymous when authentication is not configured."""
    return getattr(request.state, "principal", tenancy.ANONYMOUS)


def idempotent(request: Request, principal: tenancy.Principal, payload: object):
    """Return a stored response for a repeated `Idempotency-Key`, or None to proceed.

    Returned rather than applied as a decorator so the route keeps control of what it
    stores: the ledger entry is the thing being protected, and only the route knows
    which part of its work was the side effect.
    """
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        return None, ""

    digest = idempotency.fingerprint(payload)
    try:
        replay = idempotency.lookup(principal.tenant, key, digest)
    except idempotency.KeyReused as exc:
        # 409, not 200-with-the-old-answer. Reusing a key with a different body is a
        # client bug, and serving the previous response would hide it behind something
        # that looks like success.
        raise HTTPException(409, str(exc)) from exc

    if replay is not None:
        METRICS.inc("claimiq_idempotent_replays_total", {"tenant": principal.tenant})
        return JSONResponse(
            status_code=replay.status_code,
            content=replay.body,
            headers={"Idempotency-Replayed": "true"},
        ), digest
    return None, digest


def require(scope: str):
    """Dependency factory: 403 unless the caller holds `scope`.

    Authentication is handled by middleware and authorisation here, because the two
    answer different questions and conflating them is how an endpoint ends up
    authenticated but unauthorised -- every valid key able to do everything.
    """

    def dependency(request: Request) -> tenancy.Principal:
        principal = caller(request)
        if not principal.can(scope):
            raise HTTPException(403, f"this key does not hold the {scope!r} scope")
        return principal

    return dependency


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Never let a plain-text 500 reach the client.

    FastAPI's default renders unhandled exceptions as `Internal Server Error` in
    text/plain. The UI calls .json() on the response, so the real cause was replaced by
    a JSON parse error. Structured JSON here means the screen always shows what actually
    went wrong.
    """
    logging.getLogger("claimiq").exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"{type(exc).__name__}: {exc}",
            "path": request.url.path,
            # The one string a user can quote that finds every log line for this
            # failure. Without it a bug report is "it broke this afternoon".
            "request_id": observability.request_id.get(),
        },
        headers={"X-Request-ID": observability.request_id.get()},
    )


@app.get("/health")
def health() -> dict:
    client = try_client()
    index = get_index()
    active = providers.active_provider()
    return {
        "status": "ok",
        # Posture, stated rather than assumed. An operator who believes authentication
        # is on when it is not has a worse problem than one who knows it is off, so
        # this is unauthenticated on purpose -- it names no keys and no tenant data.
        "security": tenancy.describe(),
        "ledger_enabled": settings().ledger_enabled,
        "ai_enabled": settings().ai_enabled,
        "key_present": active.usable,
        "provider": active.name,
        "model": active.model,
        "vision_model": active.vision_model,
        "has_vision": active.has_vision,
        "model_warnings": client.verify_models() if client else ["AI off or no key"],
        "corpus_version": corpus_version(),
        "corpus_chunks": len(index.chunks),
        "dense_retrieval": index.dense_available,
    }


@app.get("/api/providers")
def list_providers() -> list[dict]:
    return providers.describe()


@app.post("/api/providers/{name}")
def switch_provider(name: str, _: tenancy.Principal = Depends(require("admin"))) -> dict:
    """Switch provider at runtime -- useful when one hits its daily quota."""
    try:
        provider = providers.set_active(name)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    # The client is cached so every node shares one instance for trace accounting;
    # swapping provider has to invalidate it or the old endpoint keeps being used.
    try_client.cache_clear()
    return {"active": provider.name, "model": provider.model, "has_vision": provider.has_vision}


@app.get("/api/profiles")
def profiles() -> dict:
    return PROFILES


@app.get("/api/samples")
def list_samples() -> list[str]:
    return sorted(p.stem for p in SAMPLES.glob("*.json"))


@app.get("/api/samples/{name}")
def get_sample(name: str) -> ClaimPacket:
    base = SAMPLES.resolve()
    path = (SAMPLES / f"{name}.json").resolve()
    if path.suffix != ".json" or not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, f"no sample named {name!r}")
    return ClaimPacket.model_validate_json(path.read_text(encoding="utf-8"))


# `response_model` on the decorator rather than a return annotation, because this can
# also return a JSONResponse on an idempotent replay. FastAPI passes a Response through
# untouched, so the documented schema stays accurate for the path that produces it --
# dropping the annotation would have quietly emptied this route's OpenAPI entry, which
# is the one integrators read first.
@app.post("/api/audit", response_model=AuditResult)
def run_audit(
    packet: ClaimPacket,
    request: Request,
    principal: tenancy.Principal = Depends(require("audit")),
):
    """Audit one claim.

    Send `Idempotency-Key` if your client retries. Without it a timeout followed by a
    retry appends a second ledger entry, and the compliance record then says this claim
    was audited twice -- the append-only store that makes retries traceable is exactly
    what makes them expensive.
    """
    replay, digest = idempotent(request, principal, packet.model_dump(mode="json"))
    if replay is not None:
        return replay

    result = audit(
        packet,
        tenant=principal.tenant,
        key_id=principal.key_id,
        request_id=observability.request_id.get(),
    )
    METRICS.inc("claimiq_audits_total", {"result": result.verdict, "mode": "single"})

    if digest:
        idempotency.remember(
            principal.tenant,
            request.headers["Idempotency-Key"].strip(),
            digest,
            200,
            result.model_dump(mode="json"),
        )
    return result


@app.post("/api/simulate-room")
def simulate(
    packet: ClaimPacket,
    profile: str = "typical",
    principal: tenancy.Principal = Depends(require("audit")),
) -> dict:
    result = audit(
        packet,
        persist=False,
        tenant=principal.tenant,
        key_id=principal.key_id,
        request_id=observability.request_id.get(),
    )
    outcome = simulate_room_downgrade(packet, result.findings, profile)
    if outcome is None:
        return {"applicable": False}
    downgraded, gain = outcome
    return {"applicable": True, "result": downgraded.model_dump(mode="json"), "gain": str(gain)}


ACCEPTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp"}

METHOD_LABEL = {
    "text_layer": "parsed directly from the PDF's table — exact, no model",
    "table+llm": "table structure detected, rows read from cells",
    "ocr+llm": "read with on-device OCR",
    "vision": "read by the vision model",
    "classified": "recognised as a supporting document — no line items expected",
    "policy": "policy schedule — settlement terms read from it",
}


async def _read_capped(file: UploadFile, limit: int) -> bytes:
    """Read the upload in chunks, refusing as soon as it exceeds `limit`."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, f"file is larger than the {limit // (1024 * 1024)} MB limit")
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/api/extract")
@app.post("/api/extract-pdf")  # legacy alias
async def extract(
    request: Request,
    file: UploadFile = File(...),
    _: tenancy.Principal = Depends(require("audit")),
) -> dict:
    """Turn an uploaded bill into structured line items.

    Everything that can fail lives inside the try, including the upload read and the
    tempfile write. Both used to sit outside it, so a failure there escaped as an
    unhandled exception -- which FastAPI renders as plain-text "Internal Server Error",
    which the UI then fed to .json(), producing the useless
    "Expecting value: line 1 column 1" instead of the actual cause.

    The heavy work is pushed to a worker thread. OCR is synchronous and CPU-bound --
    roughly 15 seconds a page -- and calling it directly from an `async def` handler
    blocks uvicorn's event loop, so the entire API stops answering mid-upload. The
    symptom is nasty and misleading: the UI's own /health poll times out and the page
    appears frozen, as if the browser had crashed rather than the server being busy.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        raise HTTPException(
            400,
            f"unsupported file type {suffix or '(none)'}. "
            f"Accepted: {', '.join(sorted(ACCEPTED_SUFFIXES))}",
        )

    # Declared size first, actual size second. Content-Length is a claim rather than a
    # fact -- it can be absent on a chunked upload and it can lie -- so it is used only
    # as a cheap early reject, and the real limit is enforced on the bytes as they
    # arrive. Reading first and checking after would mean a 2 GB upload is already
    # resident in this worker's memory by the time it is refused, which is a one-line
    # denial of service against a synchronous OCR endpoint.
    limit = settings().max_upload_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"file is larger than the {limit // (1024 * 1024)} MB limit")

    tmp_path: Path | None = None
    try:
        payload = await _read_capped(file, limit)
        if not payload:
            raise HTTPException(400, "the uploaded file is empty")

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)

        doc_id, doc_label = await run_in_threadpool(
            lambda: detect_document_type(document_text(tmp_path))
        )

        # Classification never gates extraction. Keyword guessing is weak evidence --
        # a bill listing "Vascular graft (implant)" was once scored as an implant
        # invoice and returned zero rows -- whereas actually finding a priced table is
        # strong evidence. So always try to extract, then let the result correct the guess.
        policy_terms = None
        clinical_context = None
        if doc_id == "DOC-POLICY":
            # The schedule states sum insured, room limit and co-pay outright. Reading
            # them beats making the user retype what they just uploaded.
            found = await run_in_threadpool(extract_policy_terms, tmp_path)
            policy_terms = found.model_dump() if found else None
            items, extracted = [], ExtractedBill(method="policy")
        else:
            if doc_id == "DOC-DISCHARGE-SUMMARY":
                # Same bargain as the policy branch: the summary states the dates, the
                # diagnosis and the procedure. Unlike that branch this does not skip
                # extraction -- the rule below still has to be able to correct a bill
                # that keyword scoring misfiled as a summary.
                found_context = await run_in_threadpool(extract_clinical_context, tmp_path)
                clinical_context = found_context.model_dump() if found_context else None

            try:
                items, extracted = await run_in_threadpool(extract_bill, tmp_path)
            except (APIError, LLMUnavailable) as exc:
                # The provider being unreachable is not "this document is unreadable".
                # Saying "check the page is straight" when the real problem is a 403
                # sends the user to fix their scanner instead of their API key.
                raise HTTPException(
                    503,
                    f"the AI provider could not be reached, so scans and photos cannot "
                    f"be read right now: {exc}. Native PDFs with a text layer still work.",
                ) from exc
            except Exception:  # noqa: BLE001 - a non-bill legitimately has no table
                items, extracted = [], ExtractedBill(method="classified")

        if items:
            # It priced a table, so it is the bill whatever the keywords suggested.
            doc_id, doc_label = "DOC-FINAL-BILL", "Itemised bill"
        elif doc_id is None:
            raise HTTPException(
                422,
                "could not read this document. If it is a photo or scan, check the page "
                "is straight, in focus and evenly lit.",
            )
        elif extracted.method != "policy":
            # No priced table, so it is a supporting document -- unless a branch above
            # already recorded what it did read. This used to overwrite unconditionally,
            # which made the "policy schedule" label unreachable.
            extracted.method = "classified"
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"{type(exc).__name__}: {exc}") from exc
    finally:
        # Best effort. Temp cleanup must never be able to fail a request that already
        # produced a good result -- that is exactly what WinError 32 did here.
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                logging.getLogger("claimiq").warning("could not remove temp %s", tmp_path)

    kind = (
        "Native PDF"
        if extracted.method == "text_layer"
        else "Scanned PDF"
        if suffix == ".pdf"
        else "Image"
    )
    # Derived from the rows that were actually parsed, not from a sample and not from
    # the model's own summary. None when the bill has no room row, which the UI turns
    # into a question rather than a default.
    stay = room_stay_from_items(items)

    return {
        "line_items": [i.model_dump(mode="json") for i in items],
        "room_stay": stay.model_dump(mode="json") if stay else None,
        "has_implant": any(i.head == "IMPLANT" for i in items),
        "room_category": extracted.room_category,
        "room_rate_per_day": extracted.room_rate_per_day,
        "room_days": extracted.room_days,
        "low_confidence": sum(1 for i in items if i.extract_confidence < 0.7),
        "method": extracted.method,
        "file_kind": kind,
        "how": METHOD_LABEL.get(extracted.method, extracted.method),
        "filename": file.filename,
        "document_id": doc_id,
        "document_label": doc_label,
        "policy_terms": policy_terms,
        "clinical_context": clinical_context,
    }


@app.post("/api/report")
def report(
    packet: ClaimPacket,
    profile: str = "typical",
    principal: tenancy.Principal = Depends(require("audit")),
) -> Response:
    result = audit(
        packet,
        persist=False,
        tenant=principal.tenant,
        key_id=principal.key_id,
        request_id=observability.request_id.get(),
    )
    return Response(
        content=build_report(result, profile),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{result.claim_id}-audit.pdf"'},
    )


@app.get("/api/trace/{claim_id}")
def get_trace(
    claim_id: str, principal: tenancy.Principal = Depends(require("read"))
) -> dict:
    run = trace.load_trace(claim_id, _scope(principal))
    if run is None:
        raise HTTPException(404, "no trace for that claim")
    return run.model_dump(mode="json") | {
        "total_ms": run.total_ms,
        "total_tokens": run.total_tokens,
    }


@app.get("/api/rules")
def rules(full: bool = False) -> dict:
    """The rule catalog: totals always, every rule when `full=1`.

    `full` exists so the UI can offer a browsable, searchable catalog. A rule source
    nobody can read is not inspectable whatever the README says, and 104 chunks is
    small enough to send in one response and filter in the browser -- a round trip per
    keystroke would be slower and no more correct.

    Freshness comes from the source registry rather than a hardcoded string. The old
    `verified_on: None` was a literal that no code path could ever set, so the field
    said "unverified" even after somebody had verified it.
    """
    chunks = load_corpus()
    counts: dict[str, int] = {}
    for chunk in chunks:
        key = chunk.list_name or "POLICY_WORDING"
        counts[key] = counts.get(key, 0) + 1

    stale = stale_sources()
    registry = sources()
    verified = [
        s.verified_on.isoformat()
        for s in registry.values()
        if s.verified_on is not None
    ]

    payload = {
        "corpus_version": corpus_version(),
        # The newest verification date across sources in use, or None when no source
        # has ever been checked -- which is the current state and the product says so.
        "verified_on": max(verified) if verified else None,
        "note": " ".join(
            f"{registry[sid].title}: {reason}" for sid, reason in stale.items()
        ) or "All rule sources have been verified against their issuing publication.",
        "stale_sources": stale,
        "counts": counts,
        "total": len(chunks),
        "chunk_ids": [c.chunk_id for c in chunks],
    }
    if full:
        payload["rules"] = [c.model_dump(mode="json") for c in chunks]
    return payload


@app.get("/api/rules/{chunk_id}")
def rule(chunk_id: str) -> dict:
    for chunk in load_corpus():
        if chunk.chunk_id == chunk_id:
            return chunk.model_dump()
    raise HTTPException(404, f"no chunk {chunk_id!r}")


# Every analytics route derives its tenant from the principal, never from a query
# parameter. A `?tenant=` argument would be an access-control decision handed to the
# caller, which is the same class of mistake as trusting a client-supplied user id.
@app.get("/api/analytics/summary")
def analytics_summary(principal: tenancy.Principal = Depends(require("read"))) -> dict:
    return analytics.portfolio_summary(_scope(principal))


@app.get("/api/analytics/leakage")
def analytics_leakage(principal: tenancy.Principal = Depends(require("read"))) -> list[dict]:
    return analytics.leakage_by_cause(_scope(principal)).to_dict(orient="records")


@app.get("/api/analytics/top-items")
def analytics_top_items(
    limit: int = 10, principal: tenancy.Principal = Depends(require("read"))
) -> list[dict]:
    return analytics.top_leaking_items(limit, _scope(principal)).to_dict(orient="records")


@app.get("/api/analytics/missing-docs")
def analytics_missing_docs(
    limit: int = 10, principal: tenancy.Principal = Depends(require("read"))
) -> list[dict]:
    return analytics.top_missing_documents(limit, _scope(principal)).to_dict(orient="records")


@app.get("/api/analytics/recovery")
def analytics_recovery(
    annual_claim_volume: int = 0,
    limit: int = 12,
    principal: tenancy.Principal = Depends(require("read")),
) -> dict:
    """What fixing the billing master is worth per year, and what to fix first.

    `annual_claim_volume` is the operator's own number when they have it; left at 0 the
    projection extrapolates from the run rate actually observed and says so.
    """
    return recovery.recovery_model(
        tenant=_scope(principal),
        annual_claim_volume=annual_claim_volume or None,
        limit=limit,
    )


@app.post("/api/analytics/ask")
def analytics_ask(
    payload: dict, principal: tenancy.Principal = Depends(require("read"))
) -> dict:
    question = (payload or {}).get("question", "").strip()
    if not question:
        raise HTTPException(400, "question is required")
    try:
        sql, frame, explanation = analytics.ask(question, tenant=_scope(principal))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, str(exc)) from exc
    return {
        "sql": sql,
        "explanation": explanation,
        "columns": list(frame.columns),
        "rows": frame.to_dict(orient="records"),
    }


@app.get("/api/claims")
def list_claims(
    q: str = "",
    month: str = "",
    limit: int = 50,
    offset: int = 0,
    principal: tenancy.Principal = Depends(require("read")),
) -> dict:
    """Audited claims, newest first. Search by claim id or diagnosis.

    The analytics endpoints only ever returned aggregates, so a user could see that
    their portfolio leaked money but could not open the claim it leaked on.
    """
    scope = _scope(principal)
    rows, total = store.list_claims(
        query=q, month=month, limit=limit, offset=offset, tenant=scope
    )
    return {"claims": rows, "total": total, "limit": limit, "offset": offset,
            "months": store.months(scope)}


@app.get("/api/claims/{claim_id}")
def get_claim(
    claim_id: str, principal: tenancy.Principal = Depends(require("read"))
) -> dict:
    """One stored audit, enough to render it again without re-running the engine."""
    scope = _scope(principal)
    rows, _ = store.list_claims(query=claim_id, limit=200, tenant=scope)
    claim = next((r for r in rows if r["claim_id"] == claim_id), None)
    if claim is None:
        # 404 whether it does not exist or belongs to another tenant. Distinguishing
        # them would confirm that a given claim id exists somewhere in the system.
        raise HTTPException(404, f"no stored audit for {claim_id!r}")
    return {
        **claim,
        "findings": store.claim_findings(claim_id, scope),
        "document_gaps": store.claim_gaps(claim_id, scope),
    }


# --- /v1: the integrator surface -------------------------------------------


class BatchRequest(BaseModel):
    claims: list[ClaimPacket] = Field(min_length=1)


@app.post("/v1/batches", status_code=202)
def submit_batch(
    body: BatchRequest,
    request: Request,
    principal: tenancy.Principal = Depends(require("audit")),
):
    """Queue many claims and return immediately.

    202 rather than 200, and it is not pedantry: the response describes work that has
    been accepted, not work that has been done, and a client that treats those the same
    will read `settlement: 0` off an empty job and believe it.

    Idempotency matters more here than on a single audit: a retried 4,000-claim
    submission is 4,000 duplicated ledger entries, not one.
    """
    cap = settings().max_batch_size
    if len(body.claims) > cap:
        raise HTTPException(413, f"a batch may carry at most {cap} claims; got {len(body.claims)}")

    # Duplicate ids inside one batch would produce two ledger entries and one surviving
    # database row, so the portfolio and the ledger would disagree about what happened.
    # Cheaper to refuse than to explain.
    ids = [c.claim_id for c in body.claims]
    if len(set(ids)) != len(ids):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        raise HTTPException(422, f"duplicate claim_id in batch: {', '.join(duplicates[:10])}")

    replay, digest = idempotent(request, principal, ids)
    if replay is not None:
        return replay

    job = jobs.registry().submit(body.claims, principal.tenant, principal.key_id)
    log("batch.submitted", job_id=job.job_id, claims=len(body.claims), tenant=principal.tenant)
    accepted = job.summary(include_outcomes=False) | {"poll": f"/v1/batches/{job.job_id}"}

    if digest:
        idempotency.remember(
            principal.tenant, request.headers["Idempotency-Key"].strip(), digest, 202, accepted
        )
    return accepted


@app.get("/v1/batches")
def list_batches(
    limit: int = 50, principal: tenancy.Principal = Depends(require("read"))
) -> list[dict]:
    return jobs.registry().list(_scope(principal), limit)


@app.get("/v1/batches/{job_id}")
def get_batch(job_id: str, principal: tenancy.Principal = Depends(require("read"))) -> dict:
    job = jobs.registry().get(job_id, _scope(principal))
    if job is None:
        raise HTTPException(404, f"no job {job_id!r}")
    return job.summary() | {"totals": job.totals()}


@app.post("/v1/batches/{job_id}/cancel")
def cancel_batch(job_id: str, principal: tenancy.Principal = Depends(require("audit"))) -> dict:
    """Stop after the in-flight claim. Already-audited claims keep their results --
    a cancel that discarded completed work would make it useless for the case it
    exists for, which is a batch someone submitted against the wrong month."""
    if not jobs.registry().cancel(job_id, _scope(principal)):
        raise HTTPException(404, f"no cancellable job {job_id!r}")
    return {"job_id": job_id, "state": "cancelling"}


@app.get("/v1/ledger")
def read_ledger(
    claim_id: str = "", limit: int = 100, principal: tenancy.Principal = Depends(require("read"))
) -> dict:
    return {
        "entries": ledger.history(claim_id or None, _scope(principal), min(limit, 500)),
        "head": ledger.head()[1],
    }


@app.get("/v1/ledger/verify")
def verify_ledger(_: tenancy.Principal = Depends(require("admin"))) -> dict:
    """Walk the hash chain and report the first entry that does not hold up.

    Deliberately behind `admin` and deliberately not tenant-scoped: the chain covers
    every tenant, so verifying a slice of it would verify nothing. What a tenant gets
    is their own entries plus the head hash, which they can pin externally.
    """
    status = ledger.verify_chain()
    return {
        "ok": status.ok,
        "entries": status.entries,
        "head": status.head,
        "broken_at": status.broken_at,
        "reason": status.reason,
    }


@app.post("/v1/retention/purge")
def purge(
    dry_run: bool = True, principal: tenancy.Principal = Depends(require("admin"))
) -> dict:
    """Delete claims and traces past the retention window.

    `dry_run` defaults to True and the caller must pass `dry_run=false` explicitly.
    A retention job is irreversible, it is usually run first by someone checking a
    policy rather than enforcing it, and the difference is one query parameter --
    so the destructive mode is the one you have to ask for.
    """
    report = retention.purge_expired(
        tenant=_scope(principal),
        claim_days=settings().claim_retention_days,
        trace_days=settings().trace_retention_days,
        dry_run=dry_run,
    )
    return report.as_dict()


@app.post("/v1/retention/erase/{claim_id}")
def erase(claim_id: str, principal: tenancy.Principal = Depends(require("admin"))) -> dict:
    """Erase one claim's clinical record, leaving a tombstone in the ledger.

    The chain is never broken. What survives is the arithmetic and the fact that an
    erasure happened -- see `retention.py` for why that is the right side of the
    trade, and for the limitation when claim ids themselves identify patients.
    """
    outcome = retention.erase_claim(claim_id, principal.tenant)
    if not outcome["erased"]:
        raise HTTPException(404, outcome["reason"])
    return outcome


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    """Prometheus text exposition. Public, like /health -- it carries counts, not data."""
    METRICS.gauge("claimiq_batch_queue_depth", jobs.registry().active_count())
    return PlainTextResponse(METRICS.exposition(), media_type="text/plain; version=0.0.4")


@app.get("/v1/metrics.json")
def metrics_json(_: tenancy.Principal = Depends(require("read"))) -> dict:
    """The same numbers as JSON, for the UI's operations panel."""
    return METRICS.snapshot()


def _scope(principal: tenancy.Principal) -> str | None:
    """The tenant to filter by, or None when there is only one.

    Returning None for the anonymous local principal is what keeps the demo showing
    its own history: with authentication off every audit is recorded under tenant
    'local', and filtering on it would work but would silently hide anything written
    before a key was configured.
    """
    return None if principal.anonymous else principal.tenant


# --- static frontend -------------------------------------------------------
#
# Mounted last and deliberately: Starlette matches routes in registration order,
# so every /api/* and /health route above is tried first and this only catches
# what nothing else claimed. html=True makes GET / resolve to index.html.
#
# The SPA is hash-routed (#/audit, #/trace, ...), so the browser never sends the
# server a request for those views -- the fragment after # is never transmitted.
# That means no server-side fallback route is needed for deep links; only the
# real static assets (css/js/vendor) and "/" itself need to resolve here.
#
# Gated on index.html rather than on the directory: the directory exists as an empty
# scaffold, and mounting an empty directory made GET / return a bare 404.
class RevalidatingStatics(StaticFiles):
    """Static files that a browser re-checks instead of assuming it already has.

    Starlette's StaticFiles sends `last-modified` and `etag` but no `cache-control`,
    which leaves the freshness decision to the browser's heuristic -- and Chrome's
    heuristic for a same-origin ES module is to keep using the copy it has, without
    asking. The result is that a deploy changes the server and not the screen: the API
    returns new fields, the module that renders them is three versions old, and the
    page silently omits whatever it does not know about. It looks like a backend that
    did not deploy, and no amount of reloading fixes it because a reload is exactly
    what is being served from cache.

    `no-cache` is the fix and it is not `no-store`: the browser still caches, it just
    revalidates first, so an unchanged file comes back as a 304 with no body. The cost
    is one conditional request per asset per load; the benefit is that what is on the
    screen is what is on the server.

    Genuinely immutable assets -- the fonts and images under vendor/, which are content
    that never changes in place -- keep a long TTL, because revalidating those every
    load is pure waste.
    """

    IMMUTABLE_SUFFIXES = (".woff2", ".woff", ".ttf", ".png", ".jpg", ".webp", ".ico")

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        path = str(full_path).lower()
        if path.endswith(self.IMMUTABLE_SUFFIXES) and "/vendor/" in path.replace("\\", "/"):
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["cache-control"] = "no-cache"
        return response


if (WEB / "index.html").is_file():
    app.mount("/", RevalidatingStatics(directory=WEB, html=True), name="web")
else:

    @app.get("/")
    def root() -> dict:
        """There is no SPA yet, so say where the UI actually is.

        Someone who opens the API port in a browser -- which is the natural thing to
        try -- should not get a bare 404 with nothing to act on.
        """
        return {
            "service": "ClaimIQ API",
            "ui": "run the frontend separately (cd frontend && npm run dev, http://localhost:3000) "
            "or start.ps1 -Streamlit for the legacy Streamlit UI on http://127.0.0.1:8501",
            "docs": "/docs",
            "health": "/health",
        }
