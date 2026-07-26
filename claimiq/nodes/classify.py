"""Line-item classification into the four non-payable lists.

Three interchangeable strategies, so the benchmark can compare them on identical
inputs rather than assuming the expensive one is better:

  keyword        a small hand-written table. High precision, poor recall.
  retrieval      hybrid search + a cosine threshold, no LLM.
  deterministic  keyword first, retrieval on whatever it left as payable.
                 This is what runs when AI is off.
  llm            retrieved candidates handed to the model, which must cite the
                 chunk it decided from. Uncited non-payable verdicts are discarded.

The threshold is measured, not guessed (scripts/bench_classify.py). On the labelled
set the payable and non-payable cosine distributions overlap heavily -- "OT charges"
(payable) scores higher against the catalog than "STRL GLV 7.5" (non-payable) does.
Sweeping the threshold trades one error for the other and never escapes it:

    threshold   accuracy   non-payable recall   payable wrongly deducted
    0.65          70.1%          79.7%                   50.0%
    0.70          70.1%          69.5%                   28.6%
    0.82          74.7%          62.7%                    0.0%

Wrongly disallowing a real medical charge is the worse error -- it produces a bill
the patient should never have been shown -- so the gate sits at 0.82 where that
rate is zero, and the lost recall is what the LLM is there to recover.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from claimiq.llm import LLMClient, try_client
from claimiq.retrieval.search import get_index
from claimiq.state import BEARER_OF, BillLineItem, ClaimState, Classification, ItemFinding

RETRIEVAL_THRESHOLD = 0.82
CANDIDATES_PER_ITEM = 5
LLM_BATCH_SIZE = 12

LIST_TO_CLASSIFICATION: dict[str, Classification] = {
    "LIST_I_OPTIONAL": "LIST_I_OPTIONAL",
    "LIST_II_ROOM": "LIST_II_ROOM",
    "LIST_III_PROCEDURE": "LIST_III_PROCEDURE",
    "LIST_IV_TREATMENT": "LIST_IV_TREATMENT",
}


class Determination(BaseModel):
    classification: Classification
    reason: str
    confidence: float = 1.0
    cited_chunk_id: str | None = None
    source: Literal["keyword", "retrieval", "llm"] = "keyword"


# --- strategy 1: keyword --------------------------------------------------

_KEYWORDS: list[tuple[tuple[str, ...], Classification, str]] = [
    (
        ("attendant food", "attender food", "telephone", "laundry", "toiletries",
         "baby food", "carry bag", "diaper", "sanitary", "mineral water"),
        "LIST_I_OPTIONAL",
        "Optional item. Bill directly to the patient with a signed acknowledgement.",
    ),
    (
        ("gown", "bed pan", "bedpan", "hand wash", "slipper", "tissue", "dental kit",
         "linen", "housekeeping", "admission kit", "id band"),
        "LIST_II_ROOM",
        "Subsumed into the room tariff. Billing it separately is a hospital-side loss.",
    ),
    (
        ("drape", "sterile glove", "strl glv", "gauze", "cotton", "micropore",
         "cssd", "autoclave", "surgical blade", "tourniquet", "orthobundle"),
        "LIST_III_PROCEDURE",
        "Subsumed into the procedure package rate.",
    ),
    (
        ("registration", "regn", "admission fee", "file charge", "medical record", "mrd",
         "service charge", "service chrg", "alcohol swab", "betadine", "urobag"),
        "LIST_IV_TREATMENT",
        "Subsumed into the cost of treatment.",
    ),
]


def classify_keyword(item: BillLineItem) -> Determination:
    text = item.description.lower()
    for keywords, classification, reason in _KEYWORDS:
        if any(k in text for k in keywords):
            return Determination(
                classification=classification, reason=reason, source="keyword"
            )
    return Determination(
        classification="PAYABLE", reason="No non-payable rule matched.", source="keyword"
    )


# --- strategy 2: retrieval + threshold ------------------------------------


def classify_retrieval(item: BillLineItem) -> Determination:
    hits = get_index().search(item.description, k=CANDIDATES_PER_ITEM, strategy="rrf")
    candidates = [h for h in hits if h.chunk.list_name]
    if not candidates:
        return Determination(
            classification="PAYABLE", reason="No non-payable rule matched.", source="retrieval"
        )

    best = max(candidates, key=lambda h: h.cosine)
    if best.cosine < RETRIEVAL_THRESHOLD:
        return Determination(
            classification="PAYABLE",
            reason=f"Closest catalog entry '{best.chunk.title}' scored "
            f"{best.cosine:.2f}, below the {RETRIEVAL_THRESHOLD} confidence gate.",
            confidence=float(best.cosine),
            source="retrieval",
        )

    classification = LIST_TO_CLASSIFICATION[best.chunk.list_name]
    return Determination(
        classification=classification,
        reason=f"Matched catalog entry '{best.chunk.title}'.",
        confidence=float(best.cosine),
        cited_chunk_id=best.chunk.chunk_id,
        source="retrieval",
    )


def classify_head_only(item: BillLineItem) -> Determination:
    """Control baseline: guess purely from the billing head, ignoring the description.

    This exists to expose leakage in the labelled set rather than hide it. On our
    synthetic data every non-payable item carries head=OTHER and almost every payable
    one does not, so this trivial rule scores ~99% on the payable/non-payable split
    while knowing nothing at all. Any strategy's *overall accuracy* must be read
    against this number; only `non-payable recall` -- picking the correct list among
    four, which the head cannot indicate -- measures real classification ability.
    """
    if item.head != "OTHER":
        return Determination(
            classification="PAYABLE",
            reason=f"Billed under head {item.head}, a primary service.",
            source="keyword",
        )
    return Determination(
        classification="LIST_II_ROOM",
        reason="Baseline guess: most common non-payable list.",
        source="keyword",
    )


def classify_deterministic(item: BillLineItem) -> Determination:
    """Keyword table first, retrieval on the residual. The no-key path.

    Measured better than either half alone: 74.7% accuracy at a zero false-deduction
    rate, against 73.6% for keyword-only and 51.7% for retrieval-only.
    """
    determination = classify_keyword(item)
    if determination.classification == "PAYABLE":
        return classify_retrieval(item)
    return determination


# --- strategy 4: grounded LLM ---------------------------------------------

SYSTEM = """You classify Indian hospital bill line items against a non-payable item catalog.

For each item choose exactly one:
  PAYABLE             a genuine medical charge (room, nursing, surgery, drugs, implants,
                      diagnostics, consultations, oxygen, dialysis, ambulance)
  LIST_I_OPTIONAL     personal/convenience item the PATIENT pays for
  LIST_II_ROOM        already covered by the room tariff (HOSPITAL absorbs it)
  LIST_III_PROCEDURE  already covered by the procedure package (HOSPITAL absorbs it)
  LIST_IV_TREATMENT   already covered by the cost of treatment (HOSPITAL absorbs it)
  UNMAPPED            genuinely unidentifiable, e.g. an unlabelled "MISC CHARGES" line

Rules:
- You are shown candidate catalog entries retrieved for each item. If you choose any
  LIST_* class you MUST set cited_chunk_id to the id of the entry that justifies it.
  A LIST_* verdict without a citation will be thrown away.
- The candidates are only suggestions. Retrieval always returns something; if none of
  them actually describe the item, answer PAYABLE or UNMAPPED.
- `head` is decisive. ROOM, NURSING, PROCEDURE, INVESTIGATION, PHARMACY, IMPLANT and
  CONSULTATION mark the primary billed service, which is PAYABLE. Only head=OTHER items
  are candidates for a LIST_* class.
- Do not confuse a primary service with an ancillary charge of a similar name.
  "Room charges - Single AC" (head=ROOM) is the room itself and is payable -- it is NOT
  the separate air-conditioning surcharge. "OT charges" (head=PROCEDURE) is the theatre
  charge and is payable -- it is NOT the OT booking fee or OT consumables.
- Conversely, charges for administering, monitoring or documenting care at the bedside --
  injection administration fees, pulse oximeter monitoring, chart and documentation
  charges -- are already inside the room and nursing charge. Classify them LIST_II_ROOM
  when they appear as separate head=OTHER lines.
- Never invent a chunk id."""


class ItemVerdict(BaseModel):
    line_no: int
    classification: Classification
    cited_chunk_id: str | None = None
    reason: str = Field(description="One short sentence")
    confidence: float = 1.0


class VerdictBatch(BaseModel):
    verdicts: list[ItemVerdict]


def _candidate_block(item: BillLineItem) -> str:
    hits = get_index().search(item.description, k=CANDIDATES_PER_ITEM, strategy="rrf")
    lines = [f'ITEM line_no={item.line_no} head={item.head} description="{item.description}"']
    for hit in hits:
        chunk = hit.chunk
        tag = chunk.list_name or chunk.meta.get("topic", "policy")
        aliases = ", ".join(chunk.aliases[:6])
        lines.append(
            f"  candidate {chunk.chunk_id} [{tag}] {chunk.title}"
            f"{f' (aka {aliases})' if aliases else ''}  cosine={hit.cosine:.2f}"
        )
    return "\n".join(lines)


def classify_llm(items: list[BillLineItem], client: LLMClient) -> dict[int, Determination]:
    valid_ids = {c.chunk_id for c in get_index().chunks}
    out: dict[int, Determination] = {}

    for start in range(0, len(items), LLM_BATCH_SIZE):
        batch = items[start : start + LLM_BATCH_SIZE]
        user = "\n\n".join(_candidate_block(item) for item in batch)

        result = client.structured(
            node="classify",
            system=SYSTEM,
            user=user,
            schema=VerdictBatch,
        )

        for verdict in result.verdicts:
            cited = verdict.cited_chunk_id if verdict.cited_chunk_id in valid_ids else None
            classification = verdict.classification

            # Guardrail: a non-payable verdict without a real citation is not
            # actionable, so it is downgraded rather than trusted.
            if classification.startswith("LIST_") and cited is None:
                out[verdict.line_no] = Determination(
                    classification="UNMAPPED",
                    reason=f"Model proposed {classification} but cited no valid catalog "
                    "entry, so the verdict was rejected. Needs manual review.",
                    confidence=0.0,
                    source="llm",
                )
                continue

            out[verdict.line_no] = Determination(
                classification=classification,
                reason=verdict.reason,
                confidence=verdict.confidence,
                cited_chunk_id=cited,
                source="llm",
            )

    return out


# --- node -----------------------------------------------------------------


def _to_finding(item: BillLineItem, determination: Determination) -> ItemFinding:
    return ItemFinding(
        line_no=item.line_no,
        description=item.description,
        head=item.head,
        amount=item.amount,
        classification=determination.classification,
        bearer=BEARER_OF[determination.classification],
        deducted_amount=item.amount if determination.classification.startswith("LIST_") else Decimal("0"),
        reason=determination.reason,
        confidence=determination.confidence,
        cited_chunk_id=determination.cited_chunk_id,
        source=determination.source,
    )


def classify_items(items: list[BillLineItem], strategy: str = "auto") -> list[ItemFinding]:
    """strategy: 'auto' | 'head_only' | 'keyword' | 'retrieval' | 'deterministic' | 'llm'."""
    if strategy == "head_only":
        return [_to_finding(i, classify_head_only(i)) for i in items]
    if strategy == "keyword":
        return [_to_finding(i, classify_keyword(i)) for i in items]
    if strategy == "retrieval":
        return [_to_finding(i, classify_retrieval(i)) for i in items]
    if strategy == "deterministic":
        return [_to_finding(i, classify_deterministic(i)) for i in items]

    client = try_client() if strategy in {"auto", "llm"} else None
    if client is None:
        if strategy == "llm":
            raise RuntimeError("llm strategy requested but no usable LLM client")
        return [_to_finding(i, classify_deterministic(i)) for i in items]

    try:
        verdicts = classify_llm(items, client)
    except Exception:
        if strategy == "llm":
            raise
        return [_to_finding(i, classify_deterministic(i)) for i in items]

    return [
        _to_finding(
            item,
            verdicts.get(
                item.line_no,
                Determination(
                    classification="UNMAPPED",
                    reason="Model returned no verdict for this line. Needs manual review.",
                    confidence=0.0,
                ),
            ),
        )
        for item in items
    ]


def classify_node(state: ClaimState) -> dict:
    findings = classify_items(state.packet.line_items)
    return {
        "findings": findings,
        "ai_used": any(f.cited_chunk_id for f in findings) and try_client() is not None,
    }
