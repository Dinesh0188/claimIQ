"""FastAPI surface.

The Streamlit UI talks to this over HTTP rather than importing the engine, so the
API is a real boundary and not decoration.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from openai import APIError

from claimiq import analytics, providers, trace
from claimiq.config import ROOT, settings
from claimiq.graph import audit
from claimiq.llm import LLMUnavailable, try_client
from claimiq.nodes.extract import (
    ExtractedBill,
    detect_document_type,
    document_text,
    extract_bill,
    extract_policy_terms,
)
from claimiq.report import build_report
from claimiq.retrieval.corpus import corpus_version, load_corpus
from claimiq.retrieval.search import get_index
from claimiq.state import AuditResult, ClaimPacket
from claimiq.tools.waterfall import PROFILES, simulate_room_downgrade

SAMPLES = ROOT / "data" / "samples"

app = FastAPI(title="ClaimIQ", version="1.0.0")


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
        content={"detail": f"{type(exc).__name__}: {exc}", "path": request.url.path},
    )


@app.get("/health")
def health() -> dict:
    client = try_client()
    index = get_index()
    active = providers.active_provider()
    return {
        "status": "ok",
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
def switch_provider(name: str) -> dict:
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
    path = SAMPLES / f"{name}.json"
    if not path.is_file():
        raise HTTPException(404, f"no sample named {name!r}")
    return ClaimPacket.model_validate_json(path.read_text(encoding="utf-8"))


@app.post("/api/audit")
def run_audit(packet: ClaimPacket) -> AuditResult:
    return audit(packet)


@app.post("/api/simulate-room")
def simulate(packet: ClaimPacket, profile: str = "typical") -> dict:
    result = audit(packet, persist=False)
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


@app.post("/api/extract")
@app.post("/api/extract-pdf")  # legacy alias
async def extract(file: UploadFile = File(...)) -> dict:
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

    tmp_path: Path | None = None
    try:
        payload = await file.read()
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
        if doc_id == "DOC-POLICY":
            # The schedule states sum insured, room limit and co-pay outright. Reading
            # them beats making the user retype what they just uploaded.
            found = await run_in_threadpool(extract_policy_terms, tmp_path)
            policy_terms = found.model_dump() if found else None
            items, extracted = [], ExtractedBill(method="policy")
        else:
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
        else:
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
    return {
        "line_items": [i.model_dump(mode="json") for i in items],
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
    }


@app.post("/api/report")
def report(packet: ClaimPacket, profile: str = "typical") -> Response:
    result = audit(packet, persist=False)
    return Response(
        content=build_report(result, profile),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{result.claim_id}-audit.pdf"'},
    )


@app.get("/api/trace/{claim_id}")
def get_trace(claim_id: str) -> dict:
    run = trace.load_trace(claim_id)
    if run is None:
        raise HTTPException(404, "no trace for that claim")
    return run.model_dump(mode="json") | {
        "total_ms": run.total_ms,
        "total_tokens": run.total_tokens,
    }


@app.get("/api/rules")
def rules() -> dict:
    chunks = load_corpus()
    counts: dict[str, int] = {}
    for chunk in chunks:
        key = chunk.list_name or "POLICY_WORDING"
        counts[key] = counts.get(key, 0) + 1
    return {
        "corpus_version": corpus_version(),
        "verified_on": None,
        "note": "Unverified snapshot. Not checked against current IRDAI circulars.",
        "counts": counts,
        "total": len(chunks),
    }


@app.get("/api/rules/{chunk_id}")
def rule(chunk_id: str) -> dict:
    for chunk in load_corpus():
        if chunk.chunk_id == chunk_id:
            return chunk.model_dump()
    raise HTTPException(404, f"no chunk {chunk_id!r}")


@app.get("/api/analytics/summary")
def analytics_summary() -> dict:
    return analytics.portfolio_summary()


@app.get("/api/analytics/leakage")
def analytics_leakage() -> list[dict]:
    return analytics.leakage_by_cause().to_dict(orient="records")


@app.get("/api/analytics/top-items")
def analytics_top_items(limit: int = 10) -> list[dict]:
    return analytics.top_leaking_items(limit).to_dict(orient="records")


@app.get("/api/analytics/missing-docs")
def analytics_missing_docs(limit: int = 10) -> list[dict]:
    return analytics.top_missing_documents(limit).to_dict(orient="records")


@app.post("/api/analytics/ask")
def analytics_ask(payload: dict) -> dict:
    question = (payload or {}).get("question", "").strip()
    if not question:
        raise HTTPException(400, "question is required")
    try:
        sql, frame, explanation = analytics.ask(question)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, str(exc)) from exc
    return {
        "sql": sql,
        "explanation": explanation,
        "columns": list(frame.columns),
        "rows": frame.to_dict(orient="records"),
    }
