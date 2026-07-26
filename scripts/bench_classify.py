"""Compare classification strategies on the same labelled items.

    python scripts/bench_classify.py            # keyword + retrieval
    python scripts/bench_classify.py --llm      # also the grounded LLM (needs a key)

Writes CLASSIFICATION.md. The point of this script is to make the case for the LLM
with a number rather than an assertion -- and to be willing to show that it did not
help, if it did not.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claimiq.nodes.classify import classify_items  # noqa: E402
from claimiq.retrieval.corpus import corpus_version  # noqa: E402
from claimiq.state import BillLineItem  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data" / "eval" / "classification_items.json"


def score(items, labels, strategy: str) -> dict:
    findings = classify_items(items, strategy=strategy)
    by_line = {f.line_no: f for f in findings}

    correct = 0
    non_payable_total = non_payable_hit = 0
    payable_total = payable_wrongly_deducted = 0
    cited = cited_needed = 0
    confusion: Counter = Counter()

    for line_no, expected in labels.items():
        got = by_line[line_no].classification
        if got == expected:
            correct += 1
        else:
            confusion[(expected, got)] += 1

        if expected == "PAYABLE":
            payable_total += 1
            if got.startswith("LIST_"):
                payable_wrongly_deducted += 1
        else:
            non_payable_total += 1
            if got == expected:
                non_payable_hit += 1

        if got.startswith("LIST_"):
            cited_needed += 1
            if by_line[line_no].cited_chunk_id:
                cited += 1

    total = len(labels)
    return {
        "accuracy": correct / total,
        "non_payable_recall": non_payable_hit / non_payable_total if non_payable_total else 0.0,
        "false_deduction_rate": payable_wrongly_deducted / payable_total if payable_total else 0.0,
        "citation_rate": cited / cited_needed if cited_needed else 1.0,
        "confusion": confusion,
    }


def main() -> None:
    data = json.loads(DATASET.read_text(encoding="utf-8"))["items"]
    items = [
        BillLineItem(
            line_no=i,
            description=row["description"],
            head=row["head"],
            amount=Decimal("1000"),
        )
        for i, row in enumerate(data, 1)
    ]
    labels = {i: row["expected"] for i, row in enumerate(data, 1)}

    strategies = ["head_only", "keyword", "retrieval", "deterministic"]
    if "--llm" in sys.argv:
        strategies.append("llm")

    results = {}
    for strategy in strategies:
        print(f"running {strategy} ...", flush=True)
        try:
            results[strategy] = score(items, labels, strategy)
        except Exception as exc:  # noqa: BLE001
            print(f"  {strategy} failed: {exc}")

    print(f"\ncorpus {corpus_version()} | {len(items)} labelled items\n")
    header = (
        f"{'strategy':<11} {'accuracy':>9} {'non-payable':>12} {'false ded.':>11} {'cited':>7}"
    )
    print(header)
    print("-" * len(header))
    for strategy, r in results.items():
        print(
            f"{strategy:<11} {r['accuracy']:>8.1%} {r['non_payable_recall']:>12.1%} "
            f"{r['false_deduction_rate']:>11.1%} {r['citation_rate']:>7.0%}"
        )

    print(
        "\nnon-payable = share of genuinely non-payable items given the correct list.\n"
        "false ded.  = share of genuinely PAYABLE items wrongly marked for deduction.\n"
        "              This is the costly error: it disallows a real medical charge.\n"
        "head_only   = control that ignores the description entirely. Read every\n"
        "              accuracy figure against it: the labelled set's `head` column\n"
        "              nearly determines payable-vs-non-payable on its own, so only\n"
        "              the non-payable column reflects real classification skill."
    )

    lines = [
        "# Classification benchmark",
        "",
        f"Corpus `{corpus_version()}` — {len(items)} hand-labelled line items "
        "written to imitate real hospital bill printing.",
        "",
        "| strategy | accuracy | non-payable recall | false deduction rate | citation rate |",
        "|---|---|---|---|---|",
    ]
    for strategy, r in results.items():
        lines.append(
            f"| {strategy} | {r['accuracy']:.1%} | {r['non_payable_recall']:.1%} | "
            f"{r['false_deduction_rate']:.1%} | {r['citation_rate']:.0%} |"
        )
    lines += [
        "",
        "### Read `accuracy` against the `head_only` control",
        "",
        "`head_only` ignores the item description completely and guesses from the billing "
        "head alone. It scores as high as it does because of how this labelled set is "
        "built: all 59 non-payable items carry `head=OTHER` and 27 of 28 payable ones do "
        "not, so the head column nearly determines the payable/non-payable split by "
        "itself. Overall accuracy is therefore inflated for every strategy, and a headline "
        "of \"100% accuracy\" would be close to meaningless on its own.",
        "",
        "**`non-payable recall` is the honest number.** It measures picking the correct "
        "list among four — I, II, III or IV — which the head cannot indicate at all. That "
        "column is where retrieval and the model do real work, and where the gap between "
        "strategies is genuine.",
        "",
        "**false deduction rate** is the error that matters most in production: a genuinely "
        "payable medical charge wrongly marked non-payable. Missing a non-payable item "
        "costs the hospital a recovery opportunity; wrongly disallowing a real charge "
        "produces a bill the patient should never have been shown.",
        "",
        "`retrieval` uses hybrid search with a cosine confidence gate and no LLM — this is "
        "what runs when `AI_ENABLED=false`. `llm` hands the retrieved candidates to the "
        "model, which must cite the catalog entry it decided from; uncited non-payable "
        "verdicts are rejected and downgraded to UNMAPPED.",
        "",
    ]

    worst = max(results.items(), key=lambda kv: kv[1]["accuracy"])
    lines.append(f"Best strategy on this set: **{worst[0]}** at {worst[1]['accuracy']:.1%} accuracy.")
    lines.append("")

    (ROOT / "CLASSIFICATION.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {ROOT / 'CLASSIFICATION.md'}")


if __name__ == "__main__":
    main()
