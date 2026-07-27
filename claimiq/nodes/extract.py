"""Vision-LLM extraction: bill PDF or photo -> structured line items.

No OCR binary. Pages are rasterised with pypdfium2 and handed to a multimodal model
with a schema to fill. Where the PDF has a real text layer we pass that too, as a
hint -- it costs nothing and measurably steadies the numbers.

Per-field confidence is not decoration: anything the model is unsure of is surfaced
in amber rather than silently folded into the arithmetic.
"""

from __future__ import annotations

import base64
import io
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

import pypdfium2 as pdfium
from pydantic import BaseModel, Field

from claimiq.llm import EXTRACTION_MAX_TOKENS, LLMClient, LLMUnavailable, try_client
from claimiq.state import BillLineItem, Head

MAX_PAGES = 20

# KNOWN CONSTRAINT -- vision extraction is unavailable on Groq's free tier.
#
# `qwen/qwen3.6-27b` is billed a flat ~11,111 tokens per image while the free tier
# caps a single request at 8,000 tokens per minute. One image therefore costs more
# than the entire per-minute allowance, so the request 413s immediately and no amount
# of waiting, downscaling or page-slicing gets under the bar.
#
# This is a quota ceiling, not a capability gap: the same code path works on a paid
# tier or another provider with a vision model. It matters less than it sounds,
# because extraction prefers the deterministic text-layer parse anyway, which scores
# 100% on native PDFs. Vision is only reached for true scans with no text layer.

# ~100 dpi. Kept low to reduce payload size, though measurement showed it does not
# solve the free-tier ceiling described below -- Groq reported an identical 11,111
# image tokens at scale 2.0 and at scale 1.4, i.e. it bills a flat rate per image
# regardless of resolution. Downscaling is still worth doing for upload latency.
RENDER_SCALE = 1.4

# Hard ceiling on the longest edge, for pages that are large even after scaling.
MAX_EDGE_PX = 1400

# OCR renders at a higher scale than the vision path: local OCR pays no token cost
# for resolution, and small text reads better with more pixels.
OCR_SCALE = 2.0
# Whole pages. Slicing pages into bands was tried and measured: it dropped overall
# row recall from 22% to 10% because cropping destroys the table context the model
# needs to know what a column means.
BANDS = 1

SYSTEM = """You read Indian hospital bills and return their line items as structured data.

- Transcribe every charge row exactly as printed. Do not merge, split, reorder or invent rows.
- `amount` is the row total. If only quantity and rate are printed, still report the printed
  amount column; do not compute it yourself.
- Classify each row into one `head`:
    ROOM (bed/accommodation), NURSING, PROCEDURE (surgery, OT, anaesthesia, physiotherapy),
    CONSULTATION (doctor visits), INVESTIGATION (lab, imaging), PHARMACY (drugs, IV fluids),
    IMPLANT (prosthesis, stent, plate), OTHER (everything else, including consumables,
    administrative charges and personal items).
- Skip the TOTAL / subtotal / tax summary rows. Only individual charge lines.
- Set `confidence` below 0.7 for any row whose text or figures you could not read cleanly.
- Numbers must be plain digits: "1,17,900.00" -> "117900.00". No currency symbols."""


class ExtractedRow(BaseModel):
    line_no: int
    description: str
    head: Head = "OTHER"
    quantity: str = "1"
    unit_rate: str = "0"
    amount: str
    confidence: float = 1.0


class ExtractedBill(BaseModel):
    rows: list[ExtractedRow] = Field(default_factory=list)
    room_category: str | None = None
    room_rate_per_day: str | None = None
    room_days: int | None = None
    # "text_layer", "ocr+llm" or "vision" -- surfaced so the UI never implies the
    # model read a document that pdfplumber or OCR actually parsed.
    method: str = "vision"


def _decimal(raw: str | None, default: str = "0") -> Decimal:
    if raw is None:
        return Decimal(default)
    cleaned = str(raw).replace(",", "").replace("₹", "").replace("Rs", "").strip()
    try:
        return Decimal(cleaned or default)
    except InvalidOperation:
        return Decimal(default)


def pdf_to_images(path: Path, max_pages: int = MAX_PAGES, bands: int = BANDS) -> list[str]:
    """Rasterise pages to base64 data URLs, sliced into horizontal bands.

    Bands exist because output length, not input, is what breaks these models. A
    37-row bill asked for in one shot came back as an empty generation and a
    `json_validate_failed` from the API; the same bill in bands of roughly a dozen
    rows extracts cleanly. Bands overlap slightly so a row straddling a cut is not
    lost -- duplicates are removed downstream.
    """
    urls: list[str] = []

    for image in _page_images(path, RENDER_SCALE, max_pages):
        if max(image.size) > MAX_EDGE_PX:
            ratio = MAX_EDGE_PX / max(image.size)
            image = image.resize(
                (int(image.width * ratio), int(image.height * ratio))
            )
        width, height = image.size

        if bands <= 1 or height < 900:
            urls.append(_encode(image))
            continue

        step = height // bands
        overlap = int(step * 0.08)
        for b in range(bands):
            top = max(0, b * step - overlap)
            bottom = height if b == bands - 1 else min(height, (b + 1) * step + overlap)
            urls.append(_encode(image.crop((0, top, width, bottom))))

    return urls


def _encode(image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode()}"


HEAD_FROM_DEPT = {
    "accommodation": "ROOM",
    "nursing": "NURSING",
    "surgical": "PROCEDURE",
    "procedure": "PROCEDURE",
    "professional": "CONSULTATION",
    "consult": "CONSULTATION",
    "diagnostic": "INVESTIGATION",
    "radiology": "INVESTIGATION",
    "lab": "INVESTIGATION",
    "pharmacy": "PHARMACY",
    "implant": "IMPLANT",
}


def extract_from_text_layer(path: Path, max_pages: int = MAX_PAGES) -> list[BillLineItem]:
    """Parse a native PDF's ruled table directly. No model involved.

    Measured against generated ground truth, the vision model recovered 10-22% of
    rows on these bills while reading every row it did find perfectly -- it is
    reliable at reading and unreliable at exhaustively enumerating. A ruled table
    with a real text layer is a solved problem, so it gets solved rather than
    prompted. The model is kept for the case that genuinely needs it: scans, where
    there is no text layer at all.
    """
    try:
        import pdfplumber
    except ImportError:
        return []

    items: list[BillLineItem] = []
    settings = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}

    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages[:max_pages]:
                for table in page.extract_tables(settings) or []:
                    for row in table:
                        item = _row_to_item(row, len(items) + 1)
                        if item:
                            items.append(item)
    except Exception:
        return []

    return items


def _row_to_item(row: list, line_no: int) -> BillLineItem | None:
    cells = [(c or "").strip() for c in row]
    if len(cells) < 6:
        return None

    sr, description, dept, quantity, rate, amount = cells[:6]
    if not sr.isdigit() or not description:
        return None  # header, total, or a spanning row

    parsed = _decimal(amount, "0")
    if parsed <= 0:
        return None

    dept_lower = dept.lower()
    head: Head = "OTHER"
    for token, mapped in HEAD_FROM_DEPT.items():
        if token in dept_lower:
            head = mapped  # type: ignore[assignment]
            break

    return BillLineItem(
        line_no=line_no,
        description=description,
        head=head,
        quantity=_decimal(quantity, "1"),
        unit_rate=_decimal(rate),
        amount=parsed,
        extract_confidence=1.0,
    )


def ocr_pages(path: Path, max_pages: int = MAX_PAGES) -> str:
    """Read a scanned PDF with local OCR. Free, offline, no vision model.

    RapidOCR runs on the onnxruntime that fastembed already pulls in, so this adds
    no PyTorch and no system binary -- the two reasons Tesseract and EasyOCR were
    rejected earlier.

    This exists because the vision route is not viable on a free tier: the model is
    billed a flat ~11,111 tokens per image against an 8,000/minute cap, so a single
    page cannot fit. OCR turns that same page into ~1,500 text tokens, which the
    ordinary chat model structures comfortably inside the free allowance. Cheaper,
    and it removes the vision dependency altogether.
    """
    try:
        import numpy as np
    except ImportError:
        return ""

    engine = _ocr_engine()
    if engine is None:
        return ""

    lines: list[str] = []
    for image in _page_images(path, OCR_SCALE, max_pages):
        try:
            result, _ = engine(np.array(image))
        except Exception:  # noqa: BLE001 - a bad page must not kill the upload
            continue
        if result:
            lines.extend(str(row[1]) for row in result)
    return "\n".join(lines)


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp"}

# A real claim packet is 8-10 documents, not one bill. Recognising which is which
# turns the document checklist from a form the user fills in by hand into something
# the upload answers by itself.
#
# Deliberately keyword-based, not a model call: these documents carry standard
# headings, the cost is zero, and a wrong guess is visible and correctable in the UI.
DOCUMENT_SIGNATURES: list[tuple[str, str, tuple[str, ...]]] = [
    ("DOC-FINAL-BILL", "Itemised bill", ("itemized bill", "itemised bill", "final bill", "total amount due")),
    ("DOC-DISCHARGE-SUMMARY", "Discharge summary", ("discharge summary", "condition on discharge", "final diagnosis")),
    ("DOC-CLAIM-FORM", "Claim form", ("claim form", "insured details", "declaration", "hereby declare")),
    ("DOC-PREAUTH-ENHANCEMENT", "Pre-authorisation letter", ("pre-authorization", "pre-authorisation", "preauth", "authorization no")),
    ("DOC-IMPLANT-INV", "Implant invoice", ("implant", "batch no", "hsn code", "ligation clip")),
    ("DOC-INVESTIGATION-REPORTS", "Investigation report", ("laboratory report", "pathology", "reference range", "haematology", "hematology")),
    ("DOC-OT-NOTES", "OT notes", ("operation theatre notes", "ot notes", "operative notes", "procedure performed")),
    ("DOC-PHARMACY-BILLS", "Pharmacy bill", ("pharmacy bill", "medicine bill", "drug charges")),
    ("DOC-MLC", "MLC / FIR copy", ("medico-legal", "medico legal", "mlc", "first information report")),
    ("DOC-ID-PROOF", "ID proof", ("aadhaar", "aadhar", "pan card", "passport no", "identity proof")),
    ("DOC-INDOOR-PAPERS", "Indoor case papers", ("indoor case", "case sheet", "nursing notes", "vitals chart")),
    ("DOC-POLICY", "Policy schedule", ("policy schedule", "sum insured", "important coverages", "waiting period")),
    ("DOC-DEDUCTION-SUMMARY", "TPA deduction summary", ("deduction summary", "reason for deduction", "net payable")),
]


def detect_document_type(text: str) -> tuple[str | None, str]:
    """Best-guess document id and a human label, from the document's own headings.

    Returns (None, "Unrecognised") rather than guessing when nothing matches -- an
    unrecognised document is shown to the user, not silently dropped.
    """
    haystack = " ".join(text.lower().split())
    best: tuple[int, str, str] | None = None

    for doc_id, label, keywords in DOCUMENT_SIGNATURES:
        hits = sum(1 for k in keywords if k in haystack)
        if hits and (best is None or hits > best[0]):
            best = (hits, doc_id, label)

    return (best[1], best[2]) if best else (None, "Unrecognised")


POLICY_SYSTEM = """You read Indian health insurance policy schedules and extract the terms
that determine how a claim is settled.

Return only what the document actually states. Leave a field null rather than guessing —
a wrong room limit changes the settlement by lakhs.

- Amounts as plain digits: "PHP 500,000" or "Rs. 5,00,000" -> "500000". No symbols, no commas.
- `room_rent_cap_per_day`: the per-day room limit. If stated as a percentage of sum
  insured, compute it only if the sum insured is also on this page; otherwise null.
- `copay_percent`: just the number. "10% of admissible claim" -> "10".
- If the schedule says room rent is unlimited or has no cap, use "0"."""


class ExtractedPolicy(BaseModel):
    policy_no: str | None = None
    insured_name: str | None = None
    sum_insured: str | None = None
    room_rent_cap_per_day: str | None = None
    icu_cap_per_day: str | None = None
    copay_percent: str | None = None
    notes: str = Field(default="", description="Anything material the fields cannot hold")


def extract_policy_terms(path: Path, client: LLMClient | None = None) -> ExtractedPolicy | None:
    """Read settlement terms out of an uploaded policy schedule.

    The schedule states the sum insured, room limit and co-pay outright. Asking the user
    to retype numbers that are sitting in a document they just uploaded is busywork, and
    letting a form default silently override the real contract is worse -- the room limit
    alone moves the settlement by lakhs.
    """
    client = client or try_client()
    if client is None:
        return None

    text = document_text(path, max_pages=4)
    if len(text) < 80:
        return None

    try:
        return client.structured(
            node="policy",
            system=POLICY_SYSTEM,
            user=f"Policy schedule text:\n\n{text[:8000]}",
            schema=ExtractedPolicy,
        )
    except Exception:  # noqa: BLE001 - a failed read falls back to the form defaults
        return None


def document_text(path: Path, max_pages: int = 3) -> str:
    """Cheapest readable text for classification: text layer first, OCR only if bare."""
    text = "\n".join(pdf_text_layer_pages(path, max_pages)).strip()
    if len(text) >= 60:
        return text
    return ocr_pages(path, max_pages)


def _page_images(path: Path, scale: float, max_pages: int = MAX_PAGES) -> list:
    """Rasterise a document to RGB images. Handles PDFs and plain image files.

    The `close()` is not optional on Windows. pypdfium2 holds the file open, and an
    open file cannot be deleted -- so the caller's `finally: unlink()` raised
    PermissionError (WinError 32) *after* extraction had already succeeded, and that
    exception discarded a perfectly good result. Uploads appeared to do nothing.
    """
    from PIL import Image

    if path.suffix.lower() in IMAGE_SUFFIXES:
        with Image.open(path) as img:
            return [img.convert("RGB").copy()]

    doc = pdfium.PdfDocument(str(path))
    try:
        return [
            doc[i].render(scale=scale).to_pil().convert("RGB")
            for i in range(min(len(doc), max_pages))
        ]
    finally:
        doc.close()


@lru_cache(maxsize=1)
def _ocr_engine():
    """Loaded once -- model init costs a couple of seconds."""
    try:
        from rapidocr_onnxruntime import RapidOCR

        return RapidOCR()
    except Exception:  # noqa: BLE001
        return None


OCR_SYSTEM = """You convert OCR text from an Indian hospital bill into structured line items.

The text comes from optical character recognition of a scanned bill, read roughly in
reading order. Column values for one row usually appear consecutively.

- Return one row per billed charge. Skip TOTAL, subtotal and tax summary rows.
- `amount` is the row total as printed. Strip commas and currency symbols.
- OCR sometimes joins words ("OTcharges") or drops spaces -- restore sensible spacing
  in `description`, but never invent an item that is not in the text.
- Classify each row into one `head`: ROOM, NURSING, PROCEDURE, CONSULTATION,
  INVESTIGATION, PHARMACY, IMPLANT, or OTHER.
- If a row's figures are unreadable, set confidence below 0.7 rather than guessing."""


def extract_via_ocr(
    path: Path, client: LLMClient
) -> tuple[list[BillLineItem], ExtractedBill] | None:
    """OCR the scan, then let the text model structure it. Returns None if OCR found nothing."""
    text = ocr_pages(path)
    if len(text) < 80:
        return None

    result = client.structured(
        node="extract",
        system=OCR_SYSTEM,
        user=f"OCR text from a {path.name} hospital bill:\n\n{text[:12000]}",
        schema=ExtractedBill,
        max_tokens=EXTRACTION_MAX_TOKENS,
    )

    items = [
        BillLineItem(
            line_no=row.line_no or i,
            description=row.description.strip(),
            head=row.head,
            quantity=_decimal(row.quantity, "1"),
            unit_rate=_decimal(row.unit_rate),
            amount=_decimal(row.amount),
            extract_confidence=row.confidence,
        )
        for i, row in enumerate(result.rows, 1)
    ]
    if not items:
        return None

    result.method = "ocr+llm"
    return items, result


def pdf_text_layer_pages(path: Path, max_pages: int = MAX_PAGES) -> list[str]:
    """Per-page text layer. Empty strings for pure scans, which is fine."""
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            return [(page.extract_text() or "").strip() for page in pdf.pages[:max_pages]]
    except Exception:
        return []


def extract_bill(
    path: Path, client: LLMClient | None = None, force_vision: bool = False
) -> tuple[list[BillLineItem], ExtractedBill]:
    """Cheapest path that can actually read the document.

        native PDF   -> pdfplumber table parse   (exact, instant, no model)
        scan / image -> on-device OCR -> model   (~1,500 tokens)
        vision model -> only when explicitly forced; see the note at the top of
                        this module for why it is unusable on a free tier.

    Returns the items plus the raw extraction, whose `method` records which path ran
    so the UI can state what actually happened rather than implying one route.
    """
    is_image = path.suffix.lower() in IMAGE_SUFFIXES

    # An image has no text layer to try, so skip straight to OCR.
    if not force_vision and not is_image:
        items = extract_from_text_layer(path)
        if len(items) >= 3:
            return items, ExtractedBill(method="text_layer")

    client = client or try_client()
    if client is None:
        raise LLMUnavailable(
            "This document has no readable text layer, so it needs OCR plus a model. "
            "Add an API key in providers.json, or load a sample claim instead."
        )

    # Cheapest workable route for a scan: local OCR, then the ordinary chat model.
    # Preferred over vision because it costs ~1,500 text tokens instead of ~11,111
    # image tokens, which is the difference between fitting the free tier and not.
    if not force_vision:
        ocr_result = extract_via_ocr(path, client)
        if ocr_result is not None:
            return ocr_result

    segments = pdf_to_images(path)
    if not segments:
        raise ValueError(f"no renderable pages in {path.name}")

    text = "\n".join(pdf_text_layer_pages(path)).strip()
    hint = (
        f"\n\nThe document's text layer is below for reference, but the image is "
        f"authoritative and you must only return rows visible in THIS image:\n\n{text[:5000]}"
        if text
        else "\n\nThis document has no text layer; read the image."
    )

    merged = ExtractedBill()
    failures: list[str] = []

    for index, image in enumerate(segments):
        try:
            segment = client.structured(
                node="extract",
                system=SYSTEM,
                user=(
                    f"This is horizontal slice {index + 1} of {len(segments)} of a hospital "
                    f"bill. Extract only the charge rows fully visible in this image. "
                    f"Ignore rows cut off at the top or bottom edge.{hint}"
                ),
                schema=ExtractedBill,
                images=[image],
            )
        except Exception as exc:  # noqa: BLE001 - one bad slice must not lose the rest
            failures.append(f"slice {index + 1}: {exc}")
            continue

        merged.rows.extend(segment.rows)
        merged.room_category = merged.room_category or segment.room_category
        merged.room_rate_per_day = merged.room_rate_per_day or segment.room_rate_per_day
        merged.room_days = merged.room_days or segment.room_days

    if not merged.rows:
        raise ValueError(
            f"no rows extracted from {path.name}"
            + (f" ({'; '.join(failures)})" if failures else "")
        )

    items: list[BillLineItem] = []
    seen: set[tuple[str, str]] = set()
    for row in merged.rows:
        description = row.description.strip()
        amount = _decimal(row.amount)
        # Pages can overlap on repeated headers; drop exact duplicates.
        key = (description.lower(), str(amount))
        if not description or key in seen:
            continue
        seen.add(key)
        items.append(
            BillLineItem(
                line_no=len(items) + 1,
                description=description,
                head=row.head,
                quantity=_decimal(row.quantity, "1"),
                unit_rate=_decimal(row.unit_rate),
                amount=amount,
                extract_confidence=row.confidence,
            )
        )

    return items, merged
