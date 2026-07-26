"""Render a claim packet as a realistic hospital bill PDF.

    python scripts/gen_bill_pdf.py                    # all samples
    python scripts/gen_bill_pdf.py cardiac

Because these are generated from a JSON packet we hold the ground truth, which is
what makes extraction accuracy measurable in scripts/bench_extract.py rather than
merely asserted.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from claimiq.state import ClaimPacket  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"
OUT = ROOT / "data" / "generated"

HEAD_LABEL = {
    "ROOM": "Accommodation",
    "NURSING": "Nursing",
    "PROCEDURE": "Surgical / Procedure",
    "CONSULTATION": "Professional fees",
    "INVESTIGATION": "Diagnostics",
    "PHARMACY": "Pharmacy",
    "IMPLANT": "Implants",
    "OTHER": "Other charges",
}


def build(packet: ClaimPacket, path: Path) -> None:
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=7.5, leading=9.5)
    title = ParagraphStyle("t", parent=styles["Heading1"], fontSize=15, spaceAfter=2)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Bill {packet.claim_id}",
    )

    story = [
        Paragraph("MERIDIAN MULTISPECIALITY HOSPITAL", title),
        Paragraph(
            "Plot 14, Sector 22 · Pune 411001 · Reg. MH/HOSP/00000 "
            "<br/><b>SYNTHETIC DOCUMENT — NOT A REAL PATIENT BILL</b>",
            small,
        ),
        Spacer(1, 7),
    ]

    ctx = packet.context
    meta = [
        ["Bill No", packet.claim_id, "Policy No", packet.policy.policy_id],
        ["Admission", str(ctx.admission_date), "Discharge", str(ctx.discharge_date)],
        ["Diagnosis", ctx.primary_diagnosis[:44], "Procedure", (ctx.procedure_performed or "-")[:44]],
        [
            "Room",
            f"{packet.room_stay.room_category} @ {packet.room_stay.rate_per_day}/day",
            "Days",
            str(packet.room_stay.days),
        ],
    ]
    meta_table = Table(meta, colWidths=[24 * mm, 62 * mm, 24 * mm, 60 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555555")),
                ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#555555")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("LINEBELOW", (0, -1), (-1, -1), 0.4, colors.HexColor("#999999")),
            ]
        )
    )
    story += [meta_table, Spacer(1, 8)]

    rows = [["Sr", "Particulars", "Dept", "Qty", "Rate", "Amount"]]
    for item in packet.line_items:
        rows.append(
            [
                str(item.line_no),
                item.description[:52],
                HEAD_LABEL.get(item.head, "Other charges"),
                f"{item.quantity:g}",
                f"{item.unit_rate:,.2f}",
                f"{item.amount:,.2f}",
            ]
        )
    rows.append(["", "TOTAL", "", "", "", f"{packet.gross_bill:,.2f}"])

    table = Table(
        rows,
        colWidths=[9 * mm, 74 * mm, 28 * mm, 12 * mm, 22 * mm, 25 * mm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#bbbbbb")),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
            ]
        )
    )
    story += [
        table,
        Spacer(1, 8),
        Paragraph(
            "This is a computer generated bill. Synthetic data produced for software "
            "testing; it does not describe any real person or admission.",
            small,
        ),
    ]
    doc.build(story)


def make_scan(source: Path, target: Path, dpi: int = 150) -> None:
    """Re-render a PDF as images only, destroying the text layer.

    This is how the vision path gets tested honestly. Without it every test PDF has
    a perfect text layer, pdfplumber handles all of them, and the vision model is
    never actually exercised by the benchmark.
    """
    import pypdfium2 as pdfium
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as pdfcanvas

    doc = pdfium.PdfDocument(str(source))
    page_width, page_height = A4
    pdf = pdfcanvas.Canvas(str(target), pagesize=A4)

    for index in range(len(doc)):
        image = doc[index].render(scale=dpi / 72).to_pil().convert("L")
        pdf.drawImage(
            ImageReader(image), 0, 0, width=page_width, height=page_height, preserveAspectRatio=True
        )
        pdf.showPage()

    pdf.save()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    scans = "--scan" in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    wanted = names or [p.stem for p in sorted(SAMPLES.glob("*.json"))]

    for name in wanted:
        packet = ClaimPacket.model_validate_json(
            (SAMPLES / f"{name}.json").read_text(encoding="utf-8")
        )
        path = OUT / f"{name}.pdf"
        build(packet, path)
        print(f"wrote {path}  ({len(packet.line_items)} line items, gross {packet.gross_bill:,})")

        if scans:
            scan_dir = OUT / "scans"
            scan_dir.mkdir(exist_ok=True)
            scan_path = scan_dir / f"{name}.pdf"
            make_scan(path, scan_path)
            print(f"      + {scan_path}  (image-only, no text layer)")


if __name__ == "__main__":
    main()
