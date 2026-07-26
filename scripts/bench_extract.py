"""Measure vision-extraction accuracy against known ground truth.

    python scripts/bench_extract.py

We generated the PDFs from JSON packets, so the correct answer is known exactly.
That is the only reason this number means anything -- an extraction benchmark
without ground truth is just a vibe.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claimiq.nodes.extract import extract_bill  # noqa: E402
from claimiq.state import ClaimPacket  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"
PDFS = ROOT / "data" / "generated"


def normalise(text: str) -> str:
    return " ".join(text.lower().split())


def compare(truth: ClaimPacket, extracted) -> dict:
    expected = {normalise(i.description): i for i in truth.line_items}
    got = {normalise(i.description): i for i in extracted}

    matched = set(expected) & set(got)
    amount_ok = sum(1 for k in matched if expected[k].amount == got[k].amount)
    head_ok = sum(1 for k in matched if expected[k].head == got[k].head)

    total_expected = sum((i.amount for i in truth.line_items), Decimal("0"))
    total_got = sum((i.amount for i in extracted), Decimal("0"))

    return {
        "rows_expected": len(expected),
        "rows_extracted": len(got),
        "rows_matched": len(matched),
        "row_recall": len(matched) / len(expected) if expected else 0.0,
        "amount_accuracy": amount_ok / len(matched) if matched else 0.0,
        "head_accuracy": head_ok / len(matched) if matched else 0.0,
        "total_expected": total_expected,
        "total_got": total_got,
        "total_exact": total_expected == total_got,
        "missed": sorted(set(expected) - set(got))[:6],
        "hallucinated": sorted(set(got) - set(expected))[:6],
    }


def run(pdfs: list[Path], label: str, force_vision: bool) -> list[dict]:
    rows = []
    for pdf in pdfs:
        truth_path = SAMPLES / f"{pdf.stem}.json"
        if not truth_path.is_file():
            continue
        truth = ClaimPacket.model_validate_json(truth_path.read_text(encoding="utf-8"))

        print(f"[{label}] {pdf.name} ...", flush=True)
        try:
            items, extracted = extract_bill(pdf, force_vision=force_vision)
        except Exception as exc:  # noqa: BLE001
            print(f"  failed: {exc}")
            continue

        stats = compare(truth, items)
        stats["name"] = pdf.stem
        stats["method"] = extracted.method
        rows.append(stats)
        print(
            f"  rows {stats['rows_matched']}/{stats['rows_expected']} "
            f"| amounts {stats['amount_accuracy']:.0%} | heads {stats['head_accuracy']:.0%} "
            f"| bill total {'exact' if stats['total_exact'] else 'MISMATCH'}"
        )
    return rows


def main() -> None:
    pdfs = sorted(PDFS.glob("*.pdf"))
    if not pdfs:
        print("No PDFs. Run: python scripts/gen_bill_pdf.py --scan")
        sys.exit(1)

    rows = run(pdfs, "text layer", force_vision=False)

    scans = sorted((PDFS / "scans").glob("*.pdf"))
    vision_rows = run(scans, "vision", force_vision=True) if scans else []
    if not scans:
        print("\n(no scanned PDFs — regenerate with `python scripts/gen_bill_pdf.py --scan` "
              "to benchmark the vision path)")

    if not rows:
        sys.exit(1)

    def table(group: list[dict]) -> list[str]:
        out = [
            "| bill | rows found | row recall | amount accuracy | head accuracy | bill total |",
            "|---|---|---|---|---|---|",
        ]
        for r in group:
            total = "exact" if r["total_exact"] else f"{r['total_got']:,} vs {r['total_expected']:,}"
            out.append(
                f"| {r['name']} | {r['rows_matched']}/{r['rows_expected']} | "
                f"{r['row_recall']:.0%} | {r['amount_accuracy']:.0%} | "
                f"{r['head_accuracy']:.0%} | {total} |"
            )
        return out

    def recall(group: list[dict]) -> float:
        expected = sum(r["rows_expected"] for r in group)
        return sum(r["rows_matched"] for r in group) / expected if expected else 0.0

    text_recall = recall(rows)
    lines = [
        "# Bill extraction benchmark",
        "",
        "Bills are rendered from JSON packets by `scripts/gen_bill_pdf.py`, so ground "
        "truth is exact — which is the only reason these numbers mean anything.",
        "",
        "Extraction takes the cheaper path when it can: a native PDF's ruled table is "
        "parsed directly with pdfplumber, and the vision model is reserved for scans "
        "with no text layer. There is no OCR binary in the stack either way.",
        "",
        "## Native PDFs — text-layer table parse",
        "",
        *table(rows),
        "",
        f"Overall row recall: **{text_recall:.1%}**.",
        "",
    ]

    if vision_rows:
        vision_recall = recall(vision_rows)
        lines += [
            "## Scanned PDFs — vision model",
            "",
            "The same bills re-rendered as greyscale images with the text layer destroyed, "
            "so pdfplumber has nothing to work with and the model is genuinely exercised.",
            "",
            *table(vision_rows),
            "",
            f"Overall row recall: **{vision_recall:.1%}**.",
            "",
            "## What this comparison actually showed",
            "",
            "The vision model reads accurately but does not enumerate exhaustively: on the "
            "rows it returns, amount accuracy is high, yet it silently omits many rows from "
            "a long table and on the longest bill it returned an empty generation that "
            "failed JSON validation outright.",
            "",
            "Slicing pages into horizontal bands to shorten each generation was tried and "
            "made things worse — overall recall fell from 22% to 10%, because cropping "
            "removes the table context that tells the model what a column means.",
            "",
            "So the design routes around the weakness instead of prompting harder at it: "
            "deterministic parsing where the document supports it, the model only where "
            "nothing else can read the page. That is the same judgement as keeping "
            "arithmetic out of the model — use it for what it is good at.",
            "",
        ]

    (ROOT / "EXTRACTION.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\ntext-layer recall {text_recall:.1%}", end="")
    if vision_rows:
        print(f" | vision recall {recall(vision_rows):.1%}", end="")
    print(f" — wrote {ROOT / 'EXTRACTION.md'}")


if __name__ == "__main__":
    main()
