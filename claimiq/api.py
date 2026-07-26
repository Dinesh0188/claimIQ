"""FastAPI surface.

The Streamlit UI talks to this over HTTP rather than importing the engine, so the
API is a real boundary and not decoration.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response

from claimiq import analytics, providers, trace
from claimiq.config import ROOT, settings
from claimiq.graph import audit
from claimiq.llm import try_client
from claimiq.nodes.extract import extract_bill
from claimiq.report import build_report
from claimiq.retrieval.corpus import corpus_version, load_corpus
from claimiq.retrieval.search import get_index
from claimiq.state import AuditResult, ClaimPacket
from claimiq.tools.waterfall import PROFILES, simulate_room_downgrade

SAMPLES = ROOT / "data" / "samples"

app = FastAPI(title="ClaimIQ", version="1.0.0")


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


@app.post("/api/extract-pdf")
async def extract_pdf(file: UploadFile = File(...)) -> dict:
    """Vision-LLM extraction of a bill PDF into structured line items."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "expected a .pdf upload")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)

    try:
        items, extracted = extract_bill(tmp_path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"extraction failed: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    return {
        "line_items": [i.model_dump(mode="json") for i in items],
        "room_category": extracted.room_category,
        "room_rate_per_day": extracted.room_rate_per_day,
        "room_days": extracted.room_days,
        "low_confidence": sum(1 for i in items if i.extract_confidence < 0.7),
        "method": extracted.method,
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
