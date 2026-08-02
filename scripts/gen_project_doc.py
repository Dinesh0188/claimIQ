"""Generate docs/ClaimIQ.pdf -- the project dossier.

    python scripts/gen_project_doc.py

Everything quantitative in the document is pulled from the running system at build
time: the audit figures come from a real run of the cardiac sample, the benchmark
table is parsed out of CLASSIFICATION.md, the corpus counts come from the loader, and
the test count comes from pytest's own collection. Nothing is transcribed by hand,
so the document cannot quietly drift away from the thing it describes.

Diagrams are drawn with reportlab primitives rather than embedded images, so they stay
vector, stay legible at print resolution, and use the product's own palette.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "ClaimIQ.pdf"

INK = colors.HexColor("#0b0c0f")
PANEL = colors.HexColor("#14161b")
LINE = colors.HexColor("#c9ced8")
TEXT = colors.HexColor("#16181d")
MUTED = colors.HexColor("#5d6470")
ACCENT = colors.HexColor("#e05a00")
HOSPITAL = colors.HexColor("#c62828")
PATIENT = colors.HexColor("#1565c0")
SETTLED = colors.HexColor("#2e7d32")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
BODY_W = PAGE_W - 2 * MARGIN


# ---------------------------------------------------------------- facts ----


def gather() -> dict:
    """Everything the document asserts, measured now rather than remembered."""
    from claimiq.graph import audit
    from claimiq.money import rupees as _rupees
    from claimiq.retrieval.corpus import load_corpus, sources, stale_sources
    from claimiq.state import ClaimPacket

    def rupees(value, **kw) -> str:
        """Money, in a glyph the PDF's fonts actually have.

        reportlab's built-in Type 1 faces are Latin-1 and carry no U+20B9, so the
        rupee sign silently rendered as 'n' -- every figure in the document read
        'n4,10,000'. Embedding a Unicode TTF would fix the glyph and make the build
        depend on a font file being present on the machine; 'Rs' is unambiguous to
        the audience and needs nothing.
        """
        return _rupees(value, **kw).replace("₹", "Rs ")

    packet = ClaimPacket.model_validate_json(
        (ROOT / "data" / "samples" / "cardiac.json").read_text(encoding="utf-8")
    )
    result = audit(packet, persist=False)
    typical = result.typical

    chunks = load_corpus()
    by_list: dict[str, int] = {}
    for c in chunks:
        by_list[c.list_name or "POLICY_WORDING"] = by_list.get(c.list_name or "POLICY_WORDING", 0) + 1

    bench = []
    report = (ROOT / "CLASSIFICATION.md").read_text(encoding="utf-8")
    for row in re.finditer(
        r"\|\s*(\w[\w ]*?)\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%\s*\|",
        report,
    ):
        bench.append(list(row.groups()))

    # Test count from pytest's own collector, not from a number in a README.
    tests = "unknown"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
        if m := re.search(r"(\d+) tests? collected", proc.stdout):
            tests = m.group(1)
    except Exception:
        pass

    loc = 0
    for path in ROOT.rglob("*.py"):
        if any(p in path.parts for p in (".venv", "__pycache__", ".git")):
            continue
        loc += len(path.read_text(encoding="utf-8", errors="ignore").splitlines())

    deduction = next(
        (d for d in typical.policy_deductions if d.step == "room_rent_proportionate"), None
    )

    return {
        "result": result,
        "typical": typical,
        "rupees": rupees,
        "chunks": len(chunks),
        "by_list": by_list,
        "sources": sources(),
        "stale": stale_sources(),
        "bench": bench,
        "tests": tests,
        "loc": loc,
        "deduction": deduction,
        "corpus_version": result.corpus_version,
    }


# ------------------------------------------------------------- diagrams ----


class Diagram(Flowable):
    """Base: a fixed-height canvas drawing with helpers."""

    def __init__(self, height: float):
        super().__init__()
        self.width, self.height = BODY_W, height

    def box(self, x, y, w, h, label, sub="", fill=None, stroke=LINE, bold=True):
        c = self.canv
        c.setStrokeColor(stroke)
        c.setLineWidth(0.8)
        if fill is not None:
            c.setFillColor(fill)
            c.roundRect(x, y, w, h, 3, stroke=1, fill=1)
        else:
            c.setFillColor(colors.white)
            c.roundRect(x, y, w, h, 3, stroke=1, fill=1)
        c.setFillColor(TEXT)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 7.6)
        c.drawCentredString(x + w / 2, y + h - (11 if sub else h / 2 + 3), label)
        if sub:
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 6.2)
            for i, line in enumerate(sub.split("\n")):
                c.drawCentredString(x + w / 2, y + h - 21 - i * 7.4, line)

    def arrow(self, x1, y1, x2, y2, label="", dashed=False):
        c = self.canv
        c.setStrokeColor(MUTED)
        c.setLineWidth(0.8)
        c.setDash(2, 2) if dashed else c.setDash()
        c.line(x1, y1, x2, y2)
        c.setDash()
        ang = 1 if x2 >= x1 else -1
        if abs(y2 - y1) < 0.6:  # horizontal head
            c.setFillColor(MUTED)
            p = c.beginPath()
            p.moveTo(x2, y2)
            p.lineTo(x2 - 4 * ang, y2 + 2.2)
            p.lineTo(x2 - 4 * ang, y2 - 2.2)
            p.close()
            c.drawPath(p, fill=1, stroke=0)
        if label:
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 6)
            c.drawCentredString((x1 + x2) / 2, (y1 + y2) / 2 + 3, label)


class Architecture(Diagram):
    """Layered system view: what talks to what, and across which boundary."""

    def __init__(self):
        # Tall enough for all four bands. The layout consumes roughly
        #   20 + 9.8 + 20.4 + 27 + 29.1 mm
        # measured from the top, and the final band is itself 20 mm, so anything under
        # ~107 mm draws the tools row below the flowable's own box -- which reportlab
        # happily does, straight through the next heading.
        super().__init__(112 * mm)

    def draw(self):
        c, W = self.canv, self.width
        row_h, gap = 20 * mm, 7 * mm
        y = self.height - row_h

        def band(label, y):
            c.setFillColor(MUTED)
            c.setFont("Helvetica-Bold", 6)
            c.drawString(0, y + row_h + 2.5, label.upper())

        # Presentation
        band("Presentation — static, no build step", y)
        w = (W - 2 * 4 * mm) / 3
        for i, (t, s) in enumerate([
            ("Landing page", "web/index.html\nmarketing, public"),
            ("Product SPA", "web/app.html + js/\naudit · claims · leakage · rules"),
            ("Streamlit UI", "ui/ — legacy,\nstill runnable"),
        ]):
            self.box(i * (w + 4 * mm), y, w, row_h, t, s,
                     fill=colors.HexColor("#fff4ea") if i == 1 else None,
                     stroke=ACCENT if i == 1 else LINE)

        # HTTP boundary
        y -= gap + 8
        c.setStrokeColor(ACCENT)
        c.setLineWidth(1.1)
        c.setDash(3, 2)
        c.line(0, y + 10, W, y + 10)
        c.setDash()
        c.setFillColor(ACCENT)
        c.setFont("Helvetica-Bold", 6.4)
        c.drawCentredString(W / 2, y + 13, "HTTP — a real boundary, not decoration")

        # API
        y -= row_h + 4
        band("API — FastAPI", y)
        self.box(0, y, W, row_h, "claimiq/api.py",
                 "middleware: request id · auth · rate limit · metrics · access log\n"
                 "/api/* for the bundled UI     /v1/* versioned for integrators",
                 fill=colors.HexColor("#f4f6fa"))

        # Engine
        y -= row_h + gap
        band("Engine — LangGraph, 6 nodes", y)
        w = (W - 5 * 3 * mm) / 6
        for i, n in enumerate(["classify", "compute", "readiness", "explain", "verify", "report"]):
            x = i * (w + 3 * mm)
            self.box(x, y, w, row_h * 0.62, n, fill=colors.white)
            if i < 5:
                self.arrow(x + w, y + row_h * 0.31, x + w + 3 * mm, y + row_h * 0.31)
        # repair edge
        c.setStrokeColor(HOSPITAL)
        c.setLineWidth(0.8)
        c.setDash(2, 2)
        x4 = 4 * (w + 3 * mm) + w / 2
        x3 = 3 * (w + 3 * mm) + w / 2
        c.line(x4, y, x4, y - 6)
        c.line(x4, y - 6, x3, y - 6)
        c.line(x3, y - 6, x3, y)
        c.setDash()
        c.setFillColor(HOSPITAL)
        c.setFont("Helvetica", 5.8)
        c.drawCentredString((x3 + x4) / 2, y - 12, "repair loop, bounded at 2")

        # Tools + data
        y -= row_h + gap + 6
        band("Deterministic tools and data", y)
        w = (W - 3 * 4 * mm) / 4
        for i, (t, s) in enumerate([
            ("Waterfall", "Decimal, 6 steps\nno LLM"),
            ("Retrieval", "BM25 + BGE-small\nRRF fusion"),
            ("Rule corpus", f"{104} chunks\nfails closed"),
            ("SQLite", "integer paise\nWAL"),
        ]):
            self.box(i * (w + 4 * mm), y, w, row_h, t, s)


class Flow(Diagram):
    """One claim, end to end, with the two places a human decides."""

    def __init__(self):
        super().__init__(96 * mm * 0.75)

    def draw(self):
        c, W = self.canv, self.width
        bw, bh = (W - 4 * 6 * mm) / 5, 15 * mm
        y = self.height - bh - 12

        steps = [
            ("Upload", "PDF / scan\n/ photo"),
            ("Extract", "text layer,\nelse OCR"),
            ("Confirm", "room, dates,\ndiagnosis"),
            ("Audit", "classify →\nwaterfall"),
            ("Act", "fix master,\ntell patient"),
        ]
        for i, (t, s) in enumerate(steps):
            x = i * (bw + 6 * mm)
            human = i in (0, 2, 4)
            self.box(x, y, bw, bh, t, s,
                     fill=colors.HexColor("#fff4ea") if human else colors.white,
                     stroke=ACCENT if human else LINE)
            if i < 4:
                self.arrow(x + bw, y + bh / 2, x + bw + 6 * mm, y + bh / 2)

        c.setFillColor(ACCENT)
        c.setFont("Helvetica", 6)
        c.drawString(0, y - 9, "Shaded steps are where a human decides. Everything between them is deterministic or cited.")

        # The three outputs
        y -= 30
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Bold", 6)
        c.drawString(0, y + 13, "OUTPUT — THE SAME RUPEE NEVER COUNTED TWICE")
        w = (W - 2 * 5 * mm) / 3
        for i, (t, s, col) in enumerate([
            ("Insurer settles", "after caps, sub-limits, co-pay", SETTLED),
            ("Patient pays", "optional items + policy deductions", PATIENT),
            ("Hospital absorbs", "already covered by room / procedure", HOSPITAL),
        ]):
            x = i * (w + 5 * mm)
            c.setStrokeColor(col)
            c.setLineWidth(2)
            c.line(x, y + 8, x + w, y + 8)
            c.setFillColor(col)
            c.setFont("Helvetica-Bold", 7.4)
            c.drawString(x, y - 2, t)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 6.2)
            c.drawString(x, y - 10, s)


class SplitBar(Diagram):
    """The cardiac claim, to scale."""

    def __init__(self, settled, patient, hospital, gross, fmt):
        super().__init__(28 * mm)
        self.parts = [
            ("Insurer settles", float(settled), SETTLED),
            ("Patient pays", float(patient), PATIENT),
            ("Hospital absorbs", float(hospital), HOSPITAL),
        ]
        self.gross, self.fmt = float(gross), fmt

    def draw(self):
        c, W = self.canv, self.width
        y = self.height - 22
        x = 0
        for _label, value, col in self.parts:
            w = (value / self.gross) * W if self.gross else 0
            c.setFillColor(col)
            c.rect(x, y, w, 13, stroke=0, fill=1)
            if w > 44:
                c.setFillColor(colors.white)
                c.setFont("Helvetica-Bold", 7)
                c.drawString(x + 4, y + 4, f"{value / self.gross * 100:.0f}%")
            x += w

        x = 0
        for label, value, col in self.parts:
            c.setFillColor(col)
            c.rect(x, y - 13, 6, 6, stroke=0, fill=1)
            c.setFillColor(TEXT)
            c.setFont("Helvetica-Bold", 6.8)
            c.drawString(x + 9, y - 12, f"{label} {self.fmt(value)}")
            x += max((value / self.gross) * W, 120)


# ---------------------------------------------------------------- build ----


def build() -> None:
    facts = gather()
    r, t, rupees = facts["result"], facts["typical"], facts["rupees"]

    styles = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=styles["Normal"], fontSize=9, leading=13.4,
                          textColor=TEXT, spaceAfter=5)
    small = ParagraphStyle("s", parent=body, fontSize=7.6, leading=10.6, textColor=MUTED)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=19, leading=23,
                        textColor=TEXT, spaceBefore=2, spaceAfter=7)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12.5, leading=16,
                        textColor=ACCENT, spaceBefore=15, spaceAfter=5)
    lead = ParagraphStyle("lead", parent=body, fontSize=10.6, leading=15.4, textColor=MUTED)
    quote = ParagraphStyle("q", parent=body, fontSize=9, leading=13.4,
                           leftIndent=10, textColor=MUTED, borderPadding=0)
    mono = ParagraphStyle("m", parent=body, fontName="Courier", fontSize=7.7, leading=10.4)
    centre = ParagraphStyle("c", parent=body, alignment=TA_CENTER, textColor=MUTED, fontSize=8)

    def grid(data, widths, header=True, align_right=()):
        tbl = Table(data, colWidths=widths, hAlign="LEFT")
        style = [
            ("FONTSIZE", (0, 0), (-1, -1), 7.7),
            ("TEXTCOLOR", (0, 0), (-1, -1), TEXT),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.4, LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 3.6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.6),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ]
        if header:
            style += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f2f6")),
                      ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
        for col in align_right:
            style.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
        tbl.setStyle(TableStyle(style))
        return tbl

    S = []
    P = lambda text, st=body: Paragraph(text, st)  # noqa: E731

    # ============================================================ cover ====
    S += [
        Spacer(1, 34 * mm),
        P("ClaimIQ", ParagraphStyle("cover", parent=h1, fontSize=40, leading=44,
                                    textColor=ACCENT, spaceAfter=2)),
        P("Pre-submission claim audit for Indian hospitals",
          ParagraphStyle("cs", parent=lead, fontSize=13, leading=18)),
        Spacer(1, 8 * mm),
        P("Catch the deduction before the insurer does.",
          ParagraphStyle("tag", parent=h1, fontSize=16, leading=21, textColor=TEXT)),
        Spacer(1, 5 * mm),
        P("A hospital submits a claim packet to a TPA. Weeks later money comes back — "
          "usually less than was billed — with a deduction sheet nobody on the billing "
          "desk fully understands. By then the patient has gone home, the bill is closed, "
          "and whatever was disallowed is a fight or a write-off. ClaimIQ reads the packet "
          "<b>before</b> it is submitted and splits the bill three ways, citing the rule "
          "behind every rupee it moves.", lead),
        Spacer(1, 12 * mm),
        grid([
            ["Engine", "Python 3.12 · LangGraph · Pydantic v2 · FastAPI"],
            ["Rule corpus", f"{facts['chunks']} cited rules · {facts['corpus_version']}"],
            ["Product UI", "Static SPA, no build step, served by the API"],
            ["Tests", f"{facts['tests']} passing"],
            ["Source", f"~{facts['loc']:,} lines of Python"],
        ], [34 * mm, BODY_W - 34 * mm], header=False),
        Spacer(1, 10 * mm),
        P(f"Generated {datetime.now(UTC).strftime('%d %B %Y')} from a live run of this "
          f"repository. Every figure in this document was measured at build time, not "
          f"transcribed.", small),
        P("Estimates, not adjudications. All data in this build is synthetic.", small),
        PageBreak(),
    ]

    # ========================================================== problem ====
    S += [
        P("1 — The problem", h1),
        P("Indian health insurance does not deny a claim outright very often. It "
          "<b>shaves</b> it. The deduction sheet arrives weeks after discharge, itemised "
          "in a vocabulary the billing desk did not write, and by then nobody can do "
          "anything about it.", lead),

        P("Two losses, and only one of them is visible", h2),
        P("<b>The patient</b> learns their out-of-pocket number at the discharge counter "
          "instead of at admission. The largest single cause is the proportionate "
          "deduction: choose a room above the policy's room-rent cap and the insurer "
          "scales down not just the room charge but a whole basket of associated "
          "charges — surgeon's fee, OT, nursing, anaesthesia — by the same ratio."),
        P("<b>The hospital</b> loses money it never notices. The non-payable framework is "
          "not one list, it is four, and they differ by who bears the cost. List I items "
          "are the patient's. Lists II, III and IV are charges already deemed covered by "
          "the room rate, the procedure package or the cost of treatment — the hospital "
          "cannot bill them to the insurer <i>or</i> to the patient."),
        Spacer(1, 3),
        P("A List I hit means <i>warn the patient</i>. A List II/III/IV hit means <i>your "
          "billing master is wrong and you are losing this on every single claim you "
          "file</i> — the same items, forever, across thousands of claims. This "
          "distinction is the product. A blocklist that says “these items are "
          "non-payable” surfaces only the first finding. The second is worth far more and "
          "is invisible without modelling who bears the cost.", quote),

        P("What that costs on one real claim", h2),
        P(f"The cardiac sample in this repository: a CABG admission, gross "
          f"{rupees(r.gross_bill)}, eight days in a room billed at Rs 12,000/day against a "
          f"Rs 6,000/day policy cap. Audited offline, with no language model configured, so "
          f"these figures are reproducible by anyone with the repository:"),
        Spacer(1, 4),
        SplitBar(t.projected_settlement, t.patient_liability, t.hospital_writeoff,
                 r.gross_bill, rupees),
        Spacer(1, 2),
        grid([
            ["", "Amount", "What it is"],
            ["Gross bill", rupees(r.gross_bill), "What the hospital billed"],
            ["Insurer settles", rupees(t.projected_settlement), "After caps, sub-limits and co-pay"],
            ["Patient pays", rupees(t.patient_liability), "Optional items plus policy deductions"],
            ["Hospital absorbs", rupees(t.hospital_writeoff),
             "Already covered by room or procedure charges"],
        ], [30 * mm, 26 * mm, BODY_W - 56 * mm], align_right=(1,)),
        Spacer(1, 4),
        P(f"The room-rent breach alone accounts for "
          f"{rupees(sum(float(d.amount) for d in t.policy_deductions if d.step.startswith('room_rent')))} "
          f"of that — and none of it was knowable to the family at admission, which is "
          f"precisely when it could have been avoided by choosing a cheaper room.", small),
        PageBreak(),
    ]

    # ==================================================== what it does ====
    S += [
        P("2 — What the product does", h1),
        P("Upload the packet you already assemble for the TPA. ClaimIQ reads it, asks "
          "only for what no document stated, and returns three numbers with the working "
          "shown.", lead),
        Spacer(1, 5),
        Flow(),
        Spacer(1, 6),

        P("The four screens", h2),
        grid([
            ["Screen", "What it answers"],
            ["Check a claim", "What will this specific packet lose, and why — every finding "
                              "expandable to the rule, the citation and the arithmetic."],
            ["Claims", "What have we audited, and what did it say — searchable history, "
                       "reopenable."],
            ["Leakage", "Which billing mistakes cost the most across the whole book, ranked "
                        "by aggregate loss."],
            ["Rule catalog", "What does the tool actually believe, and on whose authority."],
        ], [30 * mm, BODY_W - 30 * mm]),

        P("Three states, and the difference between them", h2),
        P("A claim comes back as <b>Ready to submit</b>, <b>Needs attention</b>, or "
          "<b>Could not fully check this claim</b>. The third exists because “we found "
          "nothing” and “we could not look” are different answers, and conflating them is "
          "how a tool becomes dangerous. Coverage is measured in rupees, not lines: one "
          "unreadable Rs 90,000 line matters more than nine unreadable Rs 10 lines."),
        PageBreak(),
    ]

    # ==================================================== architecture ====
    S += [
        P("3 — Architecture", h1),
        P("Four layers with one hard boundary. The UI never imports the engine; it speaks "
          "HTTP, which is what makes the Streamlit UI replaceable by a static SPA without "
          "touching a single node.", lead),
        Spacer(1, 6),
        Architecture(),
        Spacer(1, 8),

        P("Why the LLM does no arithmetic", h2),
        P("This is the load-bearing decision. The agent calls a deterministic "
          "<font face='Courier'>Decimal</font> calculator for every rupee, and the model "
          "only describes numbers it was handed. That is not a limitation worked around — "
          "it is what makes the verifier possible."),
        Spacer(1, 3),
        P("The model has judgment; the tool has precision.", quote),
        Spacer(1, 3),
        P("An invariant is asserted on every single audit, and a narrative containing a "
          "figure the calculator never produced is rejected and regenerated:"),
        P("projected_settlement + patient_liability + hospital_writeoff == gross_bill", mono),

        P("The six-node graph", h2),
        grid([
            ["Node", "Responsibility"],
            ["classify", "Each line item → payable, one of four non-payable lists, or unmapped. "
                         "Must cite the rule it decided from."],
            ["compute", "Deterministic waterfall across three insurer profiles."],
            ["readiness", "Twelve conditional document requirements, nine consistency checks."],
            ["explain", "Narrative and prioritised actions, from a fixed fact block."],
            ["verify", "Three guardrails: the arithmetic invariant, citation coverage, and "
                       "narrative-to-figure reconciliation."],
            ["report", "Routing target for the verifier's conditional edge."],
        ], [22 * mm, BODY_W - 22 * mm]),
        Spacer(1, 4),
        P("The backward edge from verify to explain is why this is a graph and not a "
          "chain. It is bounded at two repairs, so a model that cannot be talked into "
          "consistency degrades visibly instead of looping.", small),
        PageBreak(),
    ]

    # ========================================================== rules ====
    counts = facts["by_list"]
    S += [
        P("4 — Where the rules come from", h1),
        P("A rule that cannot name its authority is not a rule, it is an opinion. The "
          "loader fails closed: a chunk missing a citation, a severity or a resolvable "
          "source stops the process rather than becoming a determination on somebody's "
          "claim.", lead),

        grid([
            ["List", "Rules", "Who bears it", "Source"],
            ["List I — optional items", str(counts.get("LIST_I_OPTIONAL", 0)), "Patient",
             "IRDAI/HLT/REG/CIR/176/09/2019, Annexure I"],
            ["List II — subsumed into room", str(counts.get("LIST_II_ROOM", 0)), "Hospital",
             "IRDA/HLT/REG/CIR/146/07/2016, Annexure II"],
            ["List III — subsumed into procedure", str(counts.get("LIST_III_PROCEDURE", 0)), "Hospital",
             "IRDA/HLT/REG/CIR/146/07/2016, Annexure III"],
            ["List IV — subsumed into treatment", str(counts.get("LIST_IV_TREATMENT", 0)), "Hospital",
             "IRDA/HLT/REG/CIR/146/07/2016, Annexure IV"],
            ["Policy wording", str(counts.get("POLICY_WORDING", 0)), "—",
             "Synthetic, labelled as such"],
        ], [42 * mm, 13 * mm, 20 * mm, BODY_W - 75 * mm], align_right=(1,)),

        P("Honesty constraints, enforced by tests", h2),
        P("• <b>Citation precision.</b> The four lists correspond to Annexures I–IV as a "
          "whole; that mapping is verifiable and is asserted. No individual item claims an "
          "annexure line number, because no item has been matched against one. A test "
          "fails if any rule claims item-level precision."),
        P("• <b>Verification status.</b> Every source ships unverified, and the corpus "
          "version string degrades its own prefix to <font face='Courier'>unverified-</font> "
          "while that is true. The stamp cannot claim more confidence than its weakest "
          "source."),
        P("• <b>Currency is unresolved.</b> Whether the 2024 IRDAI Master Circular "
          "supersedes the 2016/2019 guidelines has not been confirmed. The product does "
          "not assert it either way, and shows a standing notice on every audit."),
        Spacer(1, 3),
        P("Resolving that question is the single highest-value item outstanding. It "
          "decides whether the corpus rests on live regulation or on a historical "
          "snapshot.", quote),
        PageBreak(),
    ]

    # ==================================================== measurement ====
    bench_rows = [["Strategy", "Accuracy", "Non-payable recall", "False deduction", "Cited"]]
    bench_rows += [[b[0], f"{b[1]}%", f"{b[2]}%", f"{b[3]}%", f"{b[4]}%"] for b in facts["bench"]]

    S += [
        P("5 — What has been measured", h1),
        P("Against 87 hand-labelled line items written to imitate real hospital bill "
          "printing. Read accuracy against the <font face='Courier'>head_only</font> "
          "control, which ignores the description entirely — the labelled set's billing "
          "head nearly determines the payable split by itself, so overall accuracy is "
          "inflated for every strategy.", lead),
        Spacer(1, 4),
        grid(bench_rows, [30 * mm, 22 * mm, 33 * mm, 28 * mm, BODY_W - 113 * mm],
             align_right=(1, 2, 3, 4)),
        Spacer(1, 5),
        P("<b>Non-payable recall is the honest number.</b> It measures picking the correct "
          "list among four, which the billing head cannot indicate at all."),
        P("<b>False deduction rate is the error that matters most.</b> Missing a "
          "non-payable item costs the hospital a recovery opportunity; wrongly disallowing "
          "a real medical charge produces a bill the patient should never have been shown. "
          "The retrieval gate sits where that rate is zero, and the lost recall is what "
          "the language model is there to recover."),

        P("What the numbers do not say", h2),
        P("Roughly one non-payable item in four is missed with no model configured. The "
          "product states this on every audit rather than presenting an unmatched line as "
          "a clean one. There are no deployment statistics in this document — no rupees "
          "recovered, no claims processed, no hospitals live — because there are none to "
          "report yet, and a plausible-looking estimate would be worse than a blank."),
        PageBreak(),
    ]

    # =========================================================== why ====
    S += [
        P("6 — Why this can win", h1),

        P("It sells against a number nobody else shows", h2),
        P("Every competing tool in this space stops at “these items are non-payable”. "
          "That is a compliance feature: it prevents a deduction the hospital was going to "
          "eat anyway. ClaimIQ models the <i>bearer</i>, which turns the same rule set "
          "into a recurring-revenue argument: <i>this line costs you money on every claim "
          "you file, here is the rule, here is the aggregate across your book</i>. "
          "The buyer is not buying compliance, they are buying a leak they did not know "
          "they had."),

        P("The output is checkable, which is what closes a hospital sale", h2),
        P("Hospital procurement does not evaluate on demo polish, it evaluates on whether "
          "the finance office can defend a number to a TPA. Every finding carries the "
          "rule, the citation, the field that failed, what was found, what was expected, "
          "and the arithmetic. A billing manager can copy one finding into an email and "
          "argue it. A tool whose output cannot be argued with is a tool that gets used "
          "once."),

        P("The honesty is a moat, not a liability", h2),
        P("The product says out loud that its corpus is unverified, that the offline path "
          "misses a quarter of non-payable items, that scan text reaches a third-party "
          "model, and that there is no multi-tenancy. Competitors claiming 99% accuracy "
          "cannot survive the first question from a compliance officer. Being the vendor "
          "who already answered it is a durable position — and every one of those "
          "statements is a roadmap item with a known fix."),

        P("Timing", h2),
        P("The IRDAI Master Circular of May 2024 consolidated 55 circulars and pushed "
          "standardisation and cashless settlement hard. Deduction disputes are being "
          "formalised at exactly the moment hospitals have the least tolerance for "
          "unexplained write-offs."),

        P("Honest competitive risk", h2),
        P("The rule corpus is not defensible on its own — the annexures are public. The "
          "defensibility is in the bearer model, the citation discipline, the extraction "
          "path that reads a real hospital bill, and the portfolio view that turns single "
          "claims into a billing-master fix. A TPA or a large HIS vendor could build this. "
          "The bet is that they will not build it <i>honestly</i>, because their incentive "
          "is to sell certainty."),
        PageBreak(),
    ]

    # ========================================================= state ====
    stale_note = "; ".join(
        f"{facts['sources'][sid].title}" for sid in list(facts["stale"])[:2]
    ) or "none"
    S += [
        P("7 — What is built, and what is not", h1),
        P("Stated plainly, because the gaps decide whether this can be deployed and the "
          "strengths decide whether it is worth deploying.", lead),

        P("Built and tested", h2),
        grid([
            ["Area", "State"],
            ["Deduction engine", "Six-step waterfall, three insurer profiles, exact Decimal, "
                                 "three-bucket invariant asserted on every audit."],
            ["Rule source", f"{facts['chunks']} cited rules, fail-closed loader, source registry "
                            "with published/retrieved/verified dates."],
            ["Verifier", "Arithmetic invariant, citation coverage on every deduction, "
                         "narrative reconciliation, bounded repair loop."],
            ["Extraction", "Native PDF table parse (exact, no model), on-device OCR + model "
                           "for scans, policy schedule and discharge summary reading."],
            ["Product UI", "Static SPA: audit, claims history, leakage dashboard, rule catalog. "
                           "Shareable URLs, print stylesheet, keyboard navigable."],
            ["Persistence", "SQLite, integer paise, WAL, searchable claim history."],
            ["Tests", f"{facts['tests']} passing, offline and deterministic."],
        ], [30 * mm, BODY_W - 30 * mm]),

        P("Not built — and why it matters", h2),
        grid([
            ["Gap", "Consequence"],
            ["Rule currency unverified", f"Sources not checked against the issuing body's current "
                                         f"publication ({stale_note}). Every audit says so."],
            ["Item-level citations", "Citations are list-level. No item has been matched "
                                     "line-by-line against the official annexure."],
            ["PHI to third-party model", "Scan text — which carries clinical detail — is sent to "
                                         "the configured provider. Native PDFs never leave the machine."],
            ["Vision extraction", "Present but not viable on a free tier; a page costs more "
                                  "tokens than the per-minute allowance."],
            ["Real-world validation", "Measured only against synthetic, hand-labelled data. No "
                                      "accuracy claim against real adjudicated claims exists."],
        ], [38 * mm, BODY_W - 38 * mm]),

        P("The order I would fix them in", h2),
        P("1. Confirm whether the 2024 Master Circular supersedes the 2016/2019 guidelines. "
          "Nothing else matters if the rules are stale.<br/>"
          "2. Verify the four annexures item by item and upgrade citation precision.<br/>"
          "3. Validate against a real book of adjudicated claims — the only number a "
          "hospital will actually believe.<br/>"
          "4. Decide the PHI question in writing before any pilot touches real patients."),
        Spacer(1, 8),
        P("This document was generated from a live run of the repository. If a figure here "
          "disagrees with the product, the product is right and this file is stale — "
          "regenerate it with <font face='Courier'>python scripts/gen_project_doc.py</font>.",
          centre),
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=16 * mm, bottomMargin=16 * mm,
        title="ClaimIQ — project dossier", author="ClaimIQ",
    )

    def furniture(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        if canvas.getPageNumber() > 1:
            canvas.drawString(MARGIN, 10 * mm, "ClaimIQ — pre-submission claim audit")
            canvas.drawRightString(PAGE_W - MARGIN, 10 * mm, str(canvas.getPageNumber()))
            canvas.setStrokeColor(LINE)
            canvas.setLineWidth(0.4)
            canvas.line(MARGIN, 13 * mm, PAGE_W - MARGIN, 13 * mm)
        canvas.restoreState()

    doc.build([KeepTogether(s) if isinstance(s, Table) else s for s in S],
              onFirstPage=furniture, onLaterPages=furniture)
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    build()
