"""Agent wiring.

LangGraph does orchestration only -- every node is a plain function
`ClaimState -> dict` and nothing in `nodes/` imports langgraph, so the framework is
swappable for a hand-rolled loop without touching a node.

    classify -> compute -> readiness -> explain -> verify -+-> report
                                          ^                |
                                          +--- repair -----+   (bounded, max 2)

The backward edge is why this is a graph and not a chain.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from langgraph.graph import END, StateGraph

from claimiq import trace
from claimiq.llm import reset_call_ledger, try_client
from claimiq.nodes.classify import classify_node
from claimiq.nodes.compute import compute_node
from claimiq.nodes.explain import explain_node
from claimiq.nodes.readiness import readiness_node
from claimiq.nodes.report import report_node
from claimiq.nodes.verify import needs_repair, verify_node
from claimiq.retrieval.corpus import corpus_version, sources, stale_sources
from claimiq.state import AuditResult, ClaimPacket, ClaimState

# Below this share of the bill actually assessed, the result is not presented as a
# finished audit. 0.9 rather than something stricter because the deterministic path
# legitimately leaves payable lines unmatched; what this catches is a bill that was
# largely unreadable or largely unrecognisable.
MIN_COVERAGE = Decimal("0.9")

NODES = {
    "classify": classify_node,
    "compute": compute_node,
    "readiness": readiness_node,
    "explain": explain_node,
    "verify": verify_node,
    "report": report_node,
}


def _traced(name: str, fn):
    """Wrap a node so its latency and token usage land in the run trace."""

    def wrapper(state: ClaimState) -> dict:
        with trace.track(name, client=try_client()):
            return fn(state)

    return wrapper


@lru_cache(maxsize=1)
def graph():
    builder = StateGraph(ClaimState)
    for name, fn in NODES.items():
        builder.add_node(name, _traced(name, fn))

    builder.set_entry_point("classify")
    builder.add_edge("classify", "compute")
    builder.add_edge("compute", "readiness")
    builder.add_edge("readiness", "explain")
    builder.add_edge("explain", "verify")
    builder.add_conditional_edges(
        "verify", needs_repair, {"explain": "explain", "report": "report"}
    )
    builder.add_edge("report", END)

    return builder.compile()


def _assessment(state: ClaimState) -> tuple[Decimal, list[str], list[str]]:
    """How much of this bill was assessed, and everything that qualifies the answer.

    Two lists, and keeping them apart is the point.

    `caveats` are facts about THIS claim that make THIS result unreliable -- most of
    the bill was unreadable, rows were misread, the verifier disagreed with itself.
    They drive the CANNOT_VERIFY verdict, because a partial check has not established
    that the parts it skipped were fine.

    `disclosures` are standing facts about the product: the offline classifier's
    measured recall, and rule sources nobody has re-checked. They are always true, so
    routing them into the verdict would make every audit CANNOT_VERIFY, and a verdict
    that never changes is one users stop reading. They belong on a persistent banner
    instead -- true, visible, and not pretending to be news about this claim.
    """
    gross = state.packet.gross_bill

    # Coverage is measured in rupees, not lines, and both ways of failing to stand
    # behind a line count against it: one we could not identify, and one we may have
    # misread. Counting them separately produced two competing signals where a
    # handful of low-confidence rows on any OCR'd bill flipped the verdict -- which is
    # how a warning becomes wallpaper. One number, weighted by what it is worth.
    unmapped_lines = [f for f in state.findings if f.classification == "UNMAPPED"]
    unmapped_nos = {f.line_no for f in unmapped_lines}
    # `extract_confidence` on the line item, NOT `confidence` on the finding. The
    # first is how well the row was READ; the second is how strongly it matched a rule,
    # and for a payable line that is the retrieval cosine -- legitimately low, and
    # nothing to do with data quality. Reading the wrong one marked every clean bill
    # unassessable.
    unsure_lines = [
        li
        for li in state.packet.line_items
        if li.extract_confidence < 0.7 and li.line_no not in unmapped_nos
    ]
    unassessed = sum(
        (f.amount for f in unmapped_lines), Decimal("0")
    ) + sum((li.amount for li in unsure_lines), Decimal("0"))
    coverage = ((gross - unassessed) / gross) if gross > 0 else Decimal("1")

    caveats: list[str] = []
    if coverage < MIN_COVERAGE:
        reasons = []
        if unmapped_lines:
            reasons.append(f"{len(unmapped_lines)} line(s) matched no rule")
        if unsure_lines:
            reasons.append(f"{len(unsure_lines)} line(s) were read with low confidence")
        caveats.append(
            f"Only {coverage:.0%} of this bill could be assessed — "
            f"{' and '.join(reasons)}, worth Rs {unassessed:,}. Unassessed charges are "
            f"counted as payable, so the settlement figure is an upper bound."
        )

    disclosures: list[str] = []
    if not state.ai_used:
        disclosures.append(
            "Checked with deterministic rules only. On the labelled benchmark that path "
            "finds 74.6% of non-payable items, so roughly one in four is missed — a line "
            "shown as payable here has not been cleared, only unmatched."
        )
    for source_id, reason in stale_sources().items():
        disclosures.append(f"Rule source '{sources()[source_id].title}' — {reason}")

    return coverage, caveats, disclosures


def audit(
    packet: ClaimPacket,
    persist: bool = True,
    *,
    tenant: str = "local",
    key_id: str = "anonymous",
    request_id: str = "",
) -> AuditResult:
    """Run the graph over one claim.

    `tenant`/`key_id`/`request_id` are provenance, not behaviour -- they change nothing
    about the determination and exist so the ledger entry can say who asked. They
    default to the local single-user values, which is what keeps `python -m claimiq.graph`
    and the whole test suite working unchanged.
    """
    # A fresh ledger per run, so token attribution starts from zero even when the
    # cached client has been used by an earlier audit in this same context.
    reset_call_ledger()
    run = trace.start_run(packet.claim_id, tenant)
    raw = graph().invoke(ClaimState(packet=packet))
    state = raw if isinstance(raw, ClaimState) else ClaimState.model_validate(raw)
    run.save()

    coverage, caveats, disclosures = _assessment(state)

    result = AuditResult(
        claim_id=packet.claim_id,
        gross_bill=packet.gross_bill,
        diagnosis=packet.context.primary_diagnosis,
        procedure=packet.context.procedure_performed or "",
        findings=state.findings,
        profiles=state.profiles,
        document_gaps=state.document_gaps,
        consistency_flags=state.consistency_flags,
        narrative=state.narrative,
        action_list=state.action_list,
        facts_json=state.facts_json,
        unmapped_count=sum(1 for f in state.findings if f.classification == "UNMAPPED"),
        ai_used=state.ai_used,
        errors=state.errors,
        corpus_version=corpus_version(),
        verify_passed=state.verify_passed,
        verify_problems=state.verify_problems,
        repair_count=state.repair_count,
        coverage=coverage,
        caveats=caveats,
        disclosures=disclosures,
        strategy="llm" if state.ai_used else "deterministic",
    )

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

    # The ledger records every determination, including the ones that are not persisted
    # to the portfolio -- `persist=False` means "this was a what-if, keep it out of the
    # dashboard", not "this never happened". A room-downgrade simulation that quietly
    # left no trace would be the one call an auditor most wants to find.
    #
    # Never fatal. A compliance sink that can fail the request it is recording turns a
    # disk-full into an outage, and the answer the user needed was already computed.
    try:
        from claimiq import ledger

        ledger.record(
            result, packet, tenant=tenant, key_id=key_id, request_id=request_id
        )
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"audit ledger write failed: {type(exc).__name__}: {exc}")

    return result


if __name__ == "__main__":
    import sys
    from pathlib import Path

    path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/samples/cardiac.json")
    result = audit(ClaimPacket.model_validate_json(path.read_text(encoding="utf-8")))

    print(f"\n{result.claim_id}  gross Rs {result.gross_bill:,}")
    print(
        f"AI: {result.ai_used}  |  unmapped: {result.unmapped_count}  |  "
        f"verify: {'PASS' if result.verify_passed else 'FAIL'}  |  repairs: {result.repair_count}"
    )
    for problem in result.verify_problems:
        print(f"  ! {problem}")

    print()
    for name, w in result.profiles.items():
        print(
            f"  {name:<13} settlement Rs {w.projected_settlement:>12,}   "
            f"patient Rs {w.patient_liability:>11,}   hospital Rs {w.hospital_writeoff:>9,}"
        )
    print(f"\nEstimated range: Rs {result.settlement_low:,} - Rs {result.settlement_high:,}")
    print(f"\n{result.narrative}\n")
    for i, action in enumerate(result.action_list, 1):
        print(f"  {i}. {action}")

    run = trace.current()
    if run:
        print(f"\n--- trace ({run.total_ms} ms, {run.total_tokens} tokens) ---")
        for node in run.nodes:
            print(
                f"  {node.node:<11} {node.latency_ms:>6} ms  calls={node.llm_calls} "
                f"cached={node.cache_hits} tok={node.prompt_tokens + node.completion_tokens}"
            )
