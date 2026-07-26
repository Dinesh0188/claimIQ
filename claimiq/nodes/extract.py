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

from claimiq.llm import LLMClient, LLMUnavailable, try_client
from claimiq.state import BillLineItem, Head

MAX_PAGES = 6

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
    doc = pdfium.PdfDocument(str(path))
    urls: list[str] = []

    for index in range(min(len(doc), max_pages)):
        image = doc[index].render(scale=RENDER_SCALE).to_pil()
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

    doc = pdfium.PdfDocument(str(path))
    lines: list[str] = []
    for index in range(min(len(doc), max_pages)):
        image = doc[index].render(scale=OCR_SCALE).to_pil().convert("RGB")
        try:
            result, _ = engine(np.array(image))
        except Exception:  # noqa: BLE001 - a bad page must not kill the upload
            continue
        if result:
            lines.extend(str(row[1]) for row in result)
    return "\n".join(lines)


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
        max_tokens=8000,
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
    """Text-layer table parse first; vision model when there is no text layer.

    Returns the items plus the raw extraction, whose `method` records which path
    ran so the UI and the benchmark can report it honestly.
    """
    if not force_vision:
        items = extract_from_text_layer(path)
        if len(items) >= 3:
            return items, ExtractedBill(method="text_layer")

    client = client or try_client()
    if client is None:
        raise LLMUnavailable(
            "This PDF has no usable text layer, so it needs OCR plus a model. "
            "Set LLM_API_KEY, or upload the claim as JSON."
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
