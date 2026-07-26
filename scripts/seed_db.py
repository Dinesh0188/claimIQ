"""Run the synthetic portfolio through the auditor and store the results.

    python scripts/seed_db.py                 # 20 via the full agent, rest deterministic
    python scripts/seed_db.py --ai 40
    python scripts/seed_db.py --ai 0          # no LLM at all

Running every claim through the full agent would be ~8 LLM calls each and will hit
Groq's free-tier rate limits, so most rows take the deterministic path. The
dashboard states the split on screen rather than implying the model saw everything.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claimiq.graph import audit  # noqa: E402
from claimiq.nodes.classify import classify_items  # noqa: E402
from claimiq.nodes.readiness import readiness_node  # noqa: E402
from claimiq.retrieval.corpus import corpus_version  # noqa: E402
from claimiq.state import AuditResult, ClaimPacket, ClaimState  # noqa: E402
from claimiq.store import reset, save_audit  # noqa: E402
from claimiq.tools.waterfall import PROFILES, compute_waterfall  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO = ROOT / "data" / "generated" / "portfolio"


def deterministic_audit(packet: ClaimPacket) -> AuditResult:
    """Same engine, no LLM: keyword+retrieval classification, then the real waterfall."""
    findings = classify_items(packet.line_items, strategy="deterministic")
    state = ClaimState(packet=packet, findings=findings)
    readiness = readiness_node(state)

    return AuditResult(
        claim_id=packet.claim_id,
        gross_bill=packet.gross_bill,
        diagnosis=packet.context.primary_diagnosis,
        procedure=packet.context.procedure_performed or "",
        findings=findings,
        profiles={p: compute_waterfall(packet, findings, p) for p in PROFILES},
        document_gaps=readiness["document_gaps"],
        consistency_flags=readiness["consistency_flags"],
        unmapped_count=sum(1 for f in findings if f.classification == "UNMAPPED"),
        ai_used=False,
        corpus_version=corpus_version(),
    )


def main() -> None:
    ai_budget = 20
    if "--ai" in sys.argv:
        ai_budget = int(sys.argv[sys.argv.index("--ai") + 1])

    paths = sorted(PORTFOLIO.glob("*.json"))
    if not paths:
        print("No portfolio found. Run: python scripts/gen_samples.py 300")
        sys.exit(1)

    reset()
    ai_done = failures = 0

    for i, path in enumerate(paths, 1):
        packet = ClaimPacket.model_validate_json(path.read_text(encoding="utf-8"))
        month = packet.context.admission_date.strftime("%Y-%m")

        use_ai = ai_done < ai_budget
        try:
            if use_ai:
                result = audit(packet, persist=False)
                ai_done += 1
            else:
                result = deterministic_audit(packet)
        except Exception as exc:  # noqa: BLE001 - one bad claim must not kill the seed
            failures += 1
            print(f"  {packet.claim_id}: {type(exc).__name__}: {exc}")
            if use_ai:
                # Rate limited or similar -- stop burning the AI budget and finish
                # the run deterministically rather than failing the whole seed.
                ai_budget = ai_done
            result = deterministic_audit(packet)

        save_audit(result, ai_pipeline=result.ai_used, month=month)

        if i % 25 == 0 or i == len(paths):
            print(f"  {i}/{len(paths)} seeded ({ai_done} via full agent)", flush=True)

    print(
        f"\ndone: {len(paths)} claims  |  {ai_done} through the full LLM agent  |  "
        f"{len(paths) - ai_done} deterministic  |  {failures} error(s) recovered"
    )


if __name__ == "__main__":
    main()
