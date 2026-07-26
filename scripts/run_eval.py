"""End-to-end evaluation. Writes EVAL.md.

    python scripts/run_eval.py

Runs the full agent over the canonical scenarios and reports, per claim:
  - whether the deterministic invariant held
  - citation coverage on non-payable determinations
  - whether the verifier passed, and how many repairs it took
  - LLM-as-judge groundedness and usefulness
  - latency, tokens and estimated cost
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claimiq import trace  # noqa: E402
from claimiq.eval.judge import judge_explanation  # noqa: E402
from claimiq.graph import audit  # noqa: E402
from claimiq.llm import try_client  # noqa: E402
from claimiq.state import ClaimPacket  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"


def main() -> None:
    client = try_client()
    if client is None:
        print("No LLM configured. Set LLM_API_KEY in .env to run the evaluation.")
        sys.exit(1)

    rows = []
    for path in sorted(SAMPLES.glob("*.json")):
        packet = ClaimPacket.model_validate_json(path.read_text(encoding="utf-8"))
        print(f"evaluating {path.stem} ...", flush=True)

        started = time.perf_counter()
        result = audit(packet, persist=False)
        elapsed = int((time.perf_counter() - started) * 1000)
        run = trace.current()

        typical = result.typical
        invariant_ok = (
            typical.projected_settlement + typical.patient_liability + typical.hospital_writeoff
            == typical.gross_bill
        )

        non_payable = [f for f in result.findings if f.classification.startswith("LIST_")]
        cited = [f for f in non_payable if f.cited_chunk_id]

        # The judge is optional. Rate limits are routine on a free tier and must not
        # discard the deterministic measurements, which are the ones that gate anything.
        try:
            verdict = judge_explanation(result, result.facts_json, client)
            grounded, useful, unsupported = (
                verdict.groundedness,
                verdict.usefulness,
                verdict.unsupported_claims,
            )
        except Exception as exc:  # noqa: BLE001
            # None, not 0. A judge that could not be reached is missing data; scoring
            # it as the worst possible grade silently defames the output.
            print(f"  judge unavailable ({type(exc).__name__}) — deterministic metrics kept")
            grounded = useful = None
            unsupported = []

        rows.append(
            {
                "name": path.stem,
                "invariant": invariant_ok,
                "citation_rate": len(cited) / len(non_payable) if non_payable else 1.0,
                "verify_passed": result.verify_passed,
                "repairs": result.repair_count,
                "groundedness": grounded,
                "usefulness": useful,
                "unsupported": unsupported,
                "latency_ms": elapsed,
                "tokens": run.total_tokens if run else 0,
                "unmapped": result.unmapped_count,
            }
        )
        judged = f"ground={grounded}/5 useful={useful}/5 " if grounded else ""
        print(
            f"  invariant={'ok' if invariant_ok else 'FAIL'} "
            f"cited={rows[-1]['citation_rate']:.0%} verify="
            f"{'pass' if result.verify_passed else 'fail'} repairs={result.repair_count} "
            f"{judged}{elapsed} ms {rows[-1]['tokens']} tok"
        )

    if not rows:
        sys.exit(1)

    n = len(rows)
    avg = lambda key: sum(r[key] for r in rows) / n  # noqa: E731

    judged = [r for r in rows if r["groundedness"] is not None]

    def judged_avg(key: str) -> str:
        if not judged:
            return "n/a"
        return f"{sum(r[key] for r in judged) / len(judged):.1f}/5"

    lines = [
        "# Evaluation",
        "",
        f"Full agent over {n} canonical scenarios. Regenerate with `python scripts/run_eval.py`.",
        "",
        "| scenario | invariant | citations | verifier | repairs | grounded | useful | latency | tokens |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['name']} | {'PASS' if r['invariant'] else 'FAIL'} | "
            f"{r['citation_rate']:.0%} | {'pass' if r['verify_passed'] else 'fail'} | "
            f"{r['repairs']} | "
            f"{f'{r_g}/5' if (r_g := r['groundedness']) is not None else 'n/a'} | "
            f"{f'{r_u}/5' if (r_u := r['usefulness']) is not None else 'n/a'} | "
            f"{r['latency_ms']:,} ms | {r['tokens']:,}{' (cached)' if not r['tokens'] else ''} |"
        )

    lines += [
        "",
        "## Aggregate",
        "",
        f"- Deterministic invariant held on **{sum(r['invariant'] for r in rows)}/{n}** scenarios",
        f"- Mean citation coverage on non-payable determinations: **{avg('citation_rate'):.1%}**",
        f"- Verifier passed on **{sum(r['verify_passed'] for r in rows)}/{n}**, "
        f"mean **{avg('repairs'):.1f}** repair(s)",
        f"- Mean groundedness **{judged_avg('groundedness')}**, usefulness "
        f"**{judged_avg('usefulness')}** (over {len(judged)}/{n} scenarios the judge answered)",
        f"- Mean latency **{avg('latency_ms'):,.0f} ms**, mean **{avg('tokens'):,.0f}** tokens "
        "per claim. Rows showing 0 tokens were served from the content-hash disk cache, "
        "so their latency reflects the cache and not the model.",
        "",
        "## What these numbers are and are not",
        "",
        "`invariant` and `citations` are deterministic checks — they either hold or they "
        "do not, and the verifier blocks output when they fail.",
        "",
        "`grounded` and `useful` come from an LLM judge. Treat them as directional: they "
        "are good at catching a regression between prompt versions and bad at establishing "
        "an absolute quality level, not least because the judge shares a family with the "
        "model being judged. They gate nothing.",
        "",
    ]

    unsupported = [c for r in rows for c in r["unsupported"]]
    if unsupported:
        lines += ["## Claims the judge flagged as unsupported", ""]
        lines += [f"- {c}" for c in unsupported[:15]]
        lines.append("")

    (ROOT / "EVAL.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {ROOT / 'EVAL.md'}")


if __name__ == "__main__":
    main()
