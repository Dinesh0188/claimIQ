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

from functools import lru_cache

from langgraph.graph import END, StateGraph

from claimiq import trace
from claimiq.llm import try_client
from claimiq.nodes.classify import classify_node
from claimiq.nodes.compute import compute_node
from claimiq.nodes.explain import explain_node
from claimiq.nodes.readiness import readiness_node
from claimiq.nodes.report import report_node
from claimiq.nodes.verify import needs_repair, verify_node
from claimiq.retrieval.corpus import corpus_version
from claimiq.state import AuditResult, ClaimPacket, ClaimState

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


def audit(packet: ClaimPacket, persist: bool = True) -> AuditResult:
    run = trace.start_run(packet.claim_id)
    raw = graph().invoke(ClaimState(packet=packet))
    state = raw if isinstance(raw, ClaimState) else ClaimState.model_validate(raw)
    run.save()

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
    )

    if persist:
        from claimiq.store import save_audit

        save_audit(result, ai_pipeline=state.ai_used)

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
