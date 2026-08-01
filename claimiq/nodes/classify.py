"""Line-item classification into the four non-payable lists.

Four interchangeable strategies, so the benchmark can compare them on identical
inputs rather than assuming the expensive one is better:

  keyword        alias match against the corpus, gated on billing head. Every hit
                 cites the chunk that produced it.
  retrieval      hybrid search + a cosine threshold, no LLM.
  deterministic  keyword first, retrieval on the residual, UNMAPPED on what neither
                 could identify. This is what runs when AI is off.
  llm            retrieved candidates handed to the model, which must cite the
                 chunk it decided from. Uncited non-payable verdicts are discarded.

`keyword` was a hand-typed table of 43 substrings that mapped straight to a class and
cited nothing. Rebuilding it from corpus aliases moved every measure at once
(scripts/bench_classify.py, 87 labelled items):

    strategy         accuracy   non-payable recall   wrongly deducted   cited
    keyword (was)      73.6%          61.0%               0.0%            0%
    keyword (now)      82.8%          74.6%               0.0%          100%
    deterministic      74.7%          62.7%               0.0%            3%
      -> now           81.6%          74.6%               0.0%          100%

The gain is not cleverness, it is coverage: the corpus carries more alias strings per
rule than anyone maintains by hand, and it carries them next to the citation.

Deterministic sits 1.2 points below keyword-only, and that is a deliberate trade, not
a regression to fix. It is the cost of being allowed to answer UNMAPPED: one labelled
payable item is now reported as unidentified rather than asserted payable. No money
moves either way -- UNMAPPED deducts nothing -- but the claim stops silently counting
lines nobody could read as clean.

The retrieval threshold is measured, not guessed. On the labelled set the payable and
non-payable cosine distributions overlap heavily -- "OT charges" (payable) scores
higher against the catalog than "STRL GLV 7.5" (non-payable) does. Sweeping the
threshold trades one error for the other and never escapes it:

    threshold   accuracy   non-payable recall   payable wrongly deducted
    0.65          70.1%          79.7%                   50.0%
    0.70          70.1%          69.5%                   28.6%
    0.82          74.7%          62.7%                    0.0%

Wrongly disallowing a real medical charge is the worse error -- it produces a bill
the patient should never have been shown -- so the gate sits at 0.82 where that
rate is zero, and the lost recall is what the LLM is there to recover.

Note what 74.6% still means: one non-payable item in four is missed with AI off. The
UI has to say so rather than presenting a no-match as a clean bill of health.
"""

from __future__ import annotations

import re
from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field

from claimiq.llm import LLMClient, try_client
from claimiq.retrieval.corpus import Chunk, load_corpus
from claimiq.retrieval.search import get_index
from claimiq.state import (
    BEARER_OF,
    BillLineItem,
    ClaimState,
    Classification,
    ItemFinding,
    Severity,
)

RETRIEVAL_THRESHOLD = 0.82
CANDIDATES_PER_ITEM = 5
# Sized against the per-minute token ceiling, not picked for tidiness. Twelve items
# with five candidates each built a ~3,350-token prompt which, plus reserved
# completion, exceeded an 8,000 TPM tier. Eight keeps the whole request under ~4,500.
LLM_BATCH_SIZE = 8

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


# --- strategy 1: alias match against the corpus ---------------------------
#
# This used to be a hand-typed table of 43 substrings mapped straight to a class, with
# no chunk id attached, and `verify.py` exempted it from citation enforcement on the
# grounds that a hand-written table is itself auditable code.
#
# The argument did not survive its own consequence. The keyword pass runs FIRST in the
# deterministic path, so the least attributable strategy shadowed the citable one: a
# substring hit produced a deduction that pointed at nothing, and the corpus rule that
# would have justified it never ran. Meanwhile the two lists drifted -- the table knew
# "bedpan" and the corpus knew "urine pot", and neither knew what the other knew.
#
# The corpus already carries the alias strings, per rule, next to the citation. So the
# matcher is built from those, and every hit carries the chunk that produced it.


@lru_cache(maxsize=1)
def _alias_index() -> list[tuple[str, re.Pattern[str], Chunk]]:
    """(alias, matcher, chunk) triples, longest alias first.

    Longest-first matters: "urine pot" and "pot" would both match a urine pot line, and
    the more specific alias is the one whose rule actually describes the item. Sorting
    by length makes the specific rule win without needing a priority column.

    MATCHING IS NOT PLAIN SUBSTRING, and the reason is a bug this file shipped for
    about an hour. Multi-word aliases are specific enough to match anywhere -- OCR
    prints "STRL GLV" and "MISC-CONS CHG", so demanding clean token boundaries would
    drop exactly the rows most likely to be non-payable. But the corpus also carries
    single short words: cap, mask, soap, belt, blade, gown. Substring-matching those
    turned "CAP AMOXYCILLIN 500MG" into a room-tariff deduction and "Capnography
    monitoring" into another, which is the one error this engine is built not to make.

    So single-token aliases match on word boundaries with an optional plural -- \\bcaps?\\b
    hits "CAP" and "caps" and misses "capsule" and "capnography" -- and multi-word
    aliases keep substring matching.

    Policy-wording chunks are excluded: they have no `list`, so there is nothing to
    classify an item as.
    """
    triples = []
    for chunk in load_corpus():
        if not chunk.list_name:
            continue
        for raw in chunk.aliases:
            alias = raw.lower().strip()
            if not alias:
                continue
            if " " in alias or "-" in alias:
                pattern = re.compile(re.escape(alias))
            else:
                pattern = re.compile(rf"\b{re.escape(alias)}s?\b")
            triples.append((alias, pattern, chunk))
    return sorted(triples, key=lambda triple: len(triple[0]), reverse=True)


def classify_alias(item: BillLineItem) -> Determination:
    """Match a line against the corpus's own alias strings.

    The billing head gates this, exactly as the LLM prompt below already requires:
    ROOM, NURSING, PROCEDURE, CONSULTATION, INVESTIGATION, PHARMACY and IMPLANT mark
    the primary billed service, and a primary service is payable whatever words appear
    in its description. Only head=OTHER lines are candidates for a non-payable list.

    Without this gate the matcher disallowed real medical charges on a word collision --
    a drug ("CAP AMOXYCILLIN") against 'cap', a diagnostic ("Shoulder blade X-ray")
    against 'blade'. The rule was already written down for the model and simply was not
    applied to the deterministic path.
    """
    if item.head != "OTHER":
        return Determination(
            classification="PAYABLE",
            reason=f"Billed under head {item.head}, a primary service.",
            source="keyword",
        )

    text = item.description.lower()
    for alias, pattern, chunk in _alias_index():
        if pattern.search(text):
            return Determination(
                classification=LIST_TO_CLASSIFICATION[chunk.list_name],
                reason=f"Matched catalog entry '{chunk.title}' on '{alias}'.",
                cited_chunk_id=chunk.chunk_id,
                source="keyword",
            )
    return Determination(
        classification="PAYABLE",
        # Deliberately not "this is payable". The deterministic path's measured
        # non-payable recall is well under 100%, so a no-match is an absence of
        # evidence and the wording has to say so -- the UI reads this string out.
        reason="No catalog entry matched this description.",
        source="keyword",
    )


# Kept under the old name because callers, benchmarks and the strategy table all use it.
classify_keyword = classify_alias


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
    """Alias match first, retrieval on the residual. The no-key path.

    Measured better than either half alone, and the one place this path is allowed to
    admit ignorance. A line that matched no rule is only called PAYABLE when its
    billing head names a primary service -- ROOM, PROCEDURE, PHARMACY and the rest are
    positive evidence that the charge is a real medical service.

    A head=OTHER line that matched nothing has no evidence either way. It used to be
    reported as PAYABLE with the reason "No non-payable rule matched", which reads as a
    clean bill of health for the exact rows most likely to be junk -- an unlabelled
    "MISC CHARGES 90,000" was silently settled as payable and never shown to anyone.
    UNMAPPED deducts nothing either, but it is counted against coverage and it says so.
    """
    determination = classify_keyword(item)
    if determination.classification != "PAYABLE":
        return determination

    determination = classify_retrieval(item)
    if determination.classification != "PAYABLE" or item.head != "OTHER":
        return determination

    return Determination(
        classification="UNMAPPED",
        reason=(
            "No catalog entry matched, and the line is not billed under a head that "
            "identifies a primary service. Needs manual review."
        ),
        confidence=determination.confidence,
        source="retrieval",
    )


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
        tag = chunk.list_name or chunk.topic or "policy"
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


@lru_cache(maxsize=1)
def _by_id() -> dict[str, Chunk]:
    return {chunk.chunk_id: chunk for chunk in load_corpus()}


# Severity for a determination that has no chunk to inherit one from.
#
# UNMAPPED is a BLOCKER and that is the point of this table: a line nobody could assess
# is the one finding that should stop a submission, and it used to be settled silently
# as payable with no finding at all.
_FALLBACK_SEVERITY: dict[str, Severity] = {
    "UNMAPPED": "BLOCKER",
    "LIST_I_OPTIONAL": "INFO",
    "LIST_II_ROOM": "WARNING",
    "LIST_III_PROCEDURE": "WARNING",
    "LIST_IV_TREATMENT": "WARNING",
    "PAYABLE": "INFO",
}


def _to_finding(item: BillLineItem, determination: Determination) -> ItemFinding:
    """Attach the rule's own attribution to the determination it produced.

    Severity and bearer are read off the cited chunk wherever there is one, so editing
    a rule in the corpus changes the product's behaviour without a code change -- which
    is the whole claim this file has to make good on. `BEARER_OF` survives only as the
    fallback for determinations with no chunk (PAYABLE, UNMAPPED).
    """
    chunk = _by_id().get(determination.cited_chunk_id or "")
    classification = determination.classification
    deducted = item.amount if classification.startswith("LIST_") else Decimal("0")

    bearer = chunk.bearer if chunk and chunk.bearer else None
    severity = chunk.severity if chunk else _FALLBACK_SEVERITY[classification]

    return ItemFinding(
        line_no=item.line_no,
        description=item.description,
        head=item.head,
        amount=item.amount,
        classification=classification,
        bearer=bearer or BEARER_OF[classification],  # type: ignore[arg-type]
        deducted_amount=deducted,
        reason=determination.reason,
        confidence=determination.confidence,
        cited_chunk_id=determination.cited_chunk_id,
        source=determination.source,
        # --- attribution ---
        rule_id=determination.cited_chunk_id or "",
        severity=severity,
        citation=chunk.attribution if chunk else "",
        source_id=chunk.source_id if chunk else "",
        field="line_items.description",
        observed=item.description,
        expected=(
            f"not billed separately — {chunk.title.lower()}"
            if chunk and classification.startswith("LIST_")
            else ""
        ),
        impact=deducted if classification.startswith("LIST_") else None,
    )


def classify_items(
    items: list[BillLineItem],
    strategy: str = "auto",
    errors: list[str] | None = None,
) -> list[ItemFinding]:
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
    except Exception as exc:  # noqa: BLE001 - a demo must not die on a 429
        if strategy == "llm":
            raise
        # Degrade, but never silently. An unreported fallback shows a smaller
        # hospital write-off than the LLM path would and looks entirely healthy,
        # which is the worst kind of failure: wrong numbers with no warning.
        if errors is not None:
            errors.append(
                f"classify: fell back to deterministic ({type(exc).__name__}). "
                "Deductions will be under-reported relative to the LLM path."
            )
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
    errors: list[str] = []
    findings = classify_items(state.packet.line_items, errors=errors)
    return {
        "findings": findings,
        "ai_used": any(f.source == "llm" for f in findings),
        "errors": [*state.errors, *errors],
    }
