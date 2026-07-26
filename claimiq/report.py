"""Downloadable audit report. reportlab, already a dependency for bill generation."""

from __future__ import annotations

import io
from datetime import UTC, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from claimiq.state import AuditResult

SEVERITY_COLOUR = {
    "BLOCKER": colors.HexColor("#b3261e"),
    "QUERY_LIKELY": colors.HexColor("#a06a00"),
    "ADVISORY": colors.HexColor("#5a5a5a"),
}


def build_report(result: AuditResult, profile: str = "typical") -> bytes:
    styles = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=styles["Normal"], fontSize=8.5, leading=11.5)
    small = ParagraphStyle("s", parent=styles["Normal"], fontSize=7, leading=9,
                           textColor=colors.HexColor("#666666"))
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=15, spaceAfter=1)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=10.5, spaceBefore=10,
                        spaceAfter=3)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"ClaimIQ audit {result.claim_id}",
    )

    wf = result.profiles[profile]
    story = [
        Paragraph("ClaimIQ — pre-submission audit", h1),
        Paragraph(
            f"Claim {result.claim_id} · generated "
            f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · "
            f"corpus <b>{result.corpus_version}</b> · profile <b>{profile}</b>",
            small,
        ),
        Spacer(1, 4),
        Paragraph(
            "<b>Estimates only.</b> Figures below estimate likely deductions under "
            "configurable insurer rules. They do not predict any specific adjudicator's "
            "decision. The rule corpus is an unverified snapshot and has not been checked "
            "against current IRDAI circulars. Source data is synthetic.",
            small,
        ),
        Spacer(1, 8),
    ]

    money = [
        ["Gross bill", f"{result.gross_bill:,.2f}"],
        ["Estimated settlement", f"{wf.projected_settlement:,.2f}"],
        ["Estimated patient liability", f"{wf.patient_liability:,.2f}"],
        ["Hospital write-off (Lists II/III/IV)", f"{wf.hospital_writeoff:,.2f}"],
        [
            "Range across insurer profiles",
            f"{result.settlement_low:,.2f}  -  {result.settlement_high:,.2f}",
        ],
    ]
    table = Table(money, colWidths=[85 * mm, 45 * mm])
    table.setStyle(
        TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, 3), (-1, 3), colors.HexColor("#b3261e")),
            ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#cccccc")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ])
    )
    story += [table, Spacer(1, 4)]

    if wf.policy_deductions:
        story.append(Paragraph("Policy-level deductions", h2))
        rows = [["Step", "Basis", "Amount"]]
        rows += [
            [d.step.replace("_", " ").title(), Paragraph(d.basis, body), f"{d.amount:,.2f}"]
            for d in wf.policy_deductions
        ]
        deductions = Table(rows, colWidths=[35 * mm, 100 * mm, 25 * mm])
        deductions.setStyle(_grid())
        story.append(deductions)

    deducted = [f for f in result.findings if f.deducted_amount > 0]
    if deducted:
        story.append(Paragraph("Item-level deductions", h2))
        rows = [["#", "Item", "List", "Borne by", "Amount", "Cited"]]
        rows += [
            [
                str(f.line_no),
                Paragraph(f.description, body),
                f.classification.replace("LIST_", "").replace("_", " "),
                f.bearer,
                f"{f.deducted_amount:,.2f}",
                f.cited_chunk_id or "-",
            ]
            for f in deducted
        ]
        items = Table(rows, colWidths=[8 * mm, 62 * mm, 26 * mm, 20 * mm, 22 * mm, 22 * mm])
        items.setStyle(_grid())
        story.append(items)

    if result.document_gaps:
        story.append(Paragraph("Missing documents", h2))
        for gap in result.document_gaps:
            story.append(
                Paragraph(
                    f'<font color="{SEVERITY_COLOUR[gap.severity].hexval()[2:]}">'
                    f"<b>[{gap.severity}]</b></font> {gap.name} — {gap.reason}",
                    body,
                )
            )

    if result.consistency_flags:
        story.append(Paragraph("Consistency flags", h2))
        for flag in result.consistency_flags:
            story.append(
                Paragraph(
                    f'<font color="{SEVERITY_COLOUR[flag.severity].hexval()[2:]}">'
                    f"<b>[{flag.severity}]</b></font> {flag.message}",
                    body,
                )
            )

    if result.action_list:
        story.append(Paragraph("Prioritised actions", h2))
        for i, action in enumerate(result.action_list, 1):
            story.append(Paragraph(f"{i}. {action}", body))

    if result.narrative:
        story.append(Paragraph("Summary", h2))
        for para in result.narrative.split("\n\n"):
            if para.strip():
                story.append(Paragraph(para.strip(), body))
                story.append(Spacer(1, 3))

    footer = (
        f"AI pipeline: {'yes' if result.ai_used else 'no (deterministic mode)'} · "
        f"verifier: {'passed' if result.verify_passed else 'failed'} · "
        f"repairs: {result.repair_count} · unmapped items: {result.unmapped_count}"
    )
    story += [Spacer(1, 8), Paragraph(footer, small)]

    doc.build([KeepTogether([]) if False else s for s in story])
    return buffer.getvalue()


def _grid() -> TableStyle:
    return TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (-2, 1), (-2, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ])
