"""Every Pydantic model in the system, plus the agent state that flows between nodes.

Money is `Decimal` throughout. Never float -- a claim auditor that is off by a paisa
because of binary floating point is worse than useless.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, computed_field

# --- vocabularies ---------------------------------------------------------

Classification = Literal[
    "PAYABLE",
    "LIST_I_OPTIONAL",
    "LIST_II_ROOM",
    "LIST_III_PROCEDURE",
    "LIST_IV_TREATMENT",
    "UNMAPPED",
]

# Who absorbs the cost. This field is the whole point of the project: a blocklist
# says "non-payable", but List I is billed to the PATIENT while Lists II/III/IV are
# a HOSPITAL billing error that leaks money on every single claim they file.
Bearer = Literal["PATIENT", "HOSPITAL", "INSURER", "UNKNOWN"]

# Billing head, used by the proportionate-deduction rule to decide which charges
# are "associated" with the room.
Head = Literal[
    "ROOM",
    "NURSING",
    "PROCEDURE",
    "CONSULTATION",
    "INVESTIGATION",
    "PHARMACY",
    "IMPLANT",
    "OTHER",
]

# How much a finding should interrupt the user.
#
#   BLOCKER  fix before submitting -- a missing document, or a line we could not assess
#   WARNING  money is moving and somebody should look -- the hospital's billing error
#   INFO     true and worth saying, but nobody has to act tonight
#
# Renamed from BLOCKER/QUERY_LIKELY/ADVISORY. The old middle term described the
# consequence at the insurer ("this will get queried") rather than the urgency here,
# which made it unusable for the corpus rules that share this vocabulary.
Severity = Literal["BLOCKER", "WARNING", "INFO"]

# Sort order, defined once. This was duplicated as a literal dict in four places --
# tools/documents.py, tools/consistency.py, ui/views/audit.py and report.py -- which is
# three opportunities for the severity ranking to disagree with itself.
SEVERITY_ORDER: dict[Severity, int] = {"BLOCKER": 0, "WARNING": 1, "INFO": 2}

BEARER_OF: dict[str, Bearer] = {
    "PAYABLE": "INSURER",
    "LIST_I_OPTIONAL": "PATIENT",
    "LIST_II_ROOM": "HOSPITAL",
    "LIST_III_PROCEDURE": "HOSPITAL",
    "LIST_IV_TREATMENT": "HOSPITAL",
    "UNMAPPED": "UNKNOWN",
}


# --- input ----------------------------------------------------------------


class BillLineItem(BaseModel):
    line_no: int
    description: str
    head: Head = "OTHER"
    quantity: Decimal = Decimal("1")
    unit_rate: Decimal = Decimal("0")
    amount: Decimal
    service_date: date | None = None
    # Set by the vision extractor; low values surface in amber rather than being trusted.
    extract_confidence: float = 1.0


class RoomStay(BaseModel):
    """Descriptive, not additive.

    Room charges live in `line_items` like everything else -- this just describes the
    room so the cap logic has something to compare against. Keeping it non-additive is
    what stops the gross bill being double-counted.
    """

    room_category: str
    rate_per_day: Decimal
    days: int
    is_icu: bool = False


class PolicyTerms(BaseModel):
    policy_id: str
    sum_insured: Decimal
    balance_sum_insured: Decimal
    room_rent_cap_per_day: Decimal | None = None
    icu_cap_per_day: Decimal | None = None
    copay_percent: Decimal = Decimal("0")
    deductible: Decimal = Decimal("0")
    procedure_sublimits: dict[str, Decimal] = Field(default_factory=dict)
    policy_inception_date: date | None = None


class ClaimContext(BaseModel):
    claim_type: Literal["cashless", "reimbursement"] = "cashless"
    admission_date: date
    discharge_date: date
    primary_diagnosis: str
    procedure_performed: str | None = None
    is_accident: bool = False
    is_maternity: bool = False
    involves_implant: bool = False
    is_ped_related: bool = False
    preauth_approved_amount: Decimal | None = None
    documents_attached: list[str] = Field(default_factory=list)


class ClaimPacket(BaseModel):
    claim_id: str
    context: ClaimContext
    policy: PolicyTerms
    room_stay: RoomStay
    line_items: list[BillLineItem]

    @property
    def gross_bill(self) -> Decimal:
        return sum((li.amount for li in self.line_items), Decimal("0"))


# --- findings -------------------------------------------------------------


class Finding(BaseModel):
    """What every check returns, whichever kind of check produced it.

    Three unrelated shapes used to come out of the engine -- an item verdict, a missing
    document, a consistency flag -- and none of them could say *what* failed, *what the
    value was*, or *what it should have been*. A billing clerk reading "pre-auth
    variance" had to go and work out which two numbers disagreed.

    `observed` and `expected` are strings rather than Decimals on purpose: they hold
    dates, document names and counts as often as they hold money, and the money that
    matters for arithmetic is in `impact`.
    """

    rule_id: str = ""
    severity: Severity = "INFO"

    # What failed, and how. Empty where a check genuinely has no single field -- an
    # item classification is about the line as a whole -- rather than filled with a
    # plausible-looking guess.
    field: str = ""
    observed: str = ""
    expected: str = ""

    # Where the rule came from. `citation` is the human-readable reference resolved
    # through corpus/SOURCES.toml; `source_id` is the key to look it up again.
    citation: str = ""
    source_id: str = ""

    # Rupees this finding moves, when that is calculable. None means "not a money
    # finding" -- distinct from Decimal("0"), which means "we checked and it is zero".
    impact: Decimal | None = None


class ItemFinding(Finding):
    line_no: int
    description: str
    head: Head
    amount: Decimal
    classification: Classification
    bearer: Bearer
    deducted_amount: Decimal
    reason: str
    confidence: float = 1.0
    # The corpus chunk this determination was grounded in. Enforced by the verifier --
    # the point of the rule is to stop the model asserting things it cannot point at.
    #
    # This used to be legitimately empty for keyword findings, on the argument that the
    # hand-written table was itself auditable code. That argument does not survive
    # contact with the consequence: an uncited keyword hit ran *first* and shadowed the
    # citable corpus rule, so the least attributable path won. The keyword matcher is
    # now built from corpus aliases and every hit carries its chunk's id.
    cited_chunk_id: str | None = None
    source: Literal["keyword", "retrieval", "llm"] = "keyword"


class PolicyDeduction(BaseModel):
    step: str
    basis: str
    amount: Decimal
    # The arithmetic, kept structured so the screen can show inputs -> formula -> result
    # instead of a bare bar on a chart. `basis` is the prose version and stays for the
    # PDF and the narrative; this is what makes the number checkable by hand.
    inputs: dict[str, str] = Field(default_factory=dict)
    formula: str = ""


class WaterfallResult(BaseModel):
    """One insurer profile's view of the same claim.

    Invariant, asserted in tests and by the verifier node:
        settlement + patient_liability + hospital_writeoff == gross_bill
    """

    profile: str
    gross_bill: Decimal
    item_deduction_total: Decimal
    policy_deductions: list[PolicyDeduction]
    projected_settlement: Decimal
    patient_liability: Decimal
    hospital_writeoff: Decimal


class DocumentGap(Finding):
    document_id: str
    name: str
    reason: str


class ConsistencyFlag(Finding):
    check_id: str
    message: str


class AuditResult(BaseModel):
    """What the UI and the API return."""

    claim_id: str
    gross_bill: Decimal
    diagnosis: str = ""
    procedure: str = ""
    findings: list[ItemFinding]
    profiles: dict[str, WaterfallResult]
    typical_profile: str = "typical"
    document_gaps: list[DocumentGap] = Field(default_factory=list)
    consistency_flags: list[ConsistencyFlag] = Field(default_factory=list)
    narrative: str = ""
    action_list: list[str] = Field(default_factory=list)
    facts_json: str = ""
    unmapped_count: int = 0
    ai_used: bool = False
    # Non-fatal degradations, surfaced in the UI. A fallback that no one is told
    # about produces plausible-looking wrong numbers.
    errors: list[str] = Field(default_factory=list)
    corpus_version: str = "unversioned"
    verify_passed: bool = True
    verify_problems: list[str] = Field(default_factory=list)
    repair_count: int = 0

    # How much of the bill was actually assessed against a rule, 0..1. This is the
    # number that separates "we checked and it is clean" from "we could not check it".
    # Without it a claim whose bill was 80% unreadable renders identically to a claim
    # that passed every check.
    coverage: Decimal = Decimal("1")
    # Reasons THIS result should not be read as authoritative -- most of the bill was
    # unreadable, rows were misread. These drive the CANNOT_VERIFY verdict.
    caveats: list[str] = Field(default_factory=list)
    # Standing facts about the product: the offline classifier's measured recall, rule
    # sources nobody has re-checked. Always true, so they must NOT drive the verdict --
    # a verdict that never changes is one nobody reads. Shown as a persistent banner.
    disclosures: list[str] = Field(default_factory=list)
    # Which strategy actually decided the classifications, so the UI can state that the
    # deterministic path misses roughly one non-payable item in four.
    strategy: str = "deterministic"

    @property
    def settlement_low(self) -> Decimal:
        return min(w.projected_settlement for w in self.profiles.values())

    @property
    def settlement_high(self) -> Decimal:
        return max(w.projected_settlement for w in self.profiles.values())

    @property
    def typical(self) -> WaterfallResult:
        return self.profiles[self.typical_profile]

    @property
    def all_findings(self) -> list[Finding]:
        """Every finding of every kind, most severe first.

        One list, so the UI and the PDF stop deriving their own order and stop
        disagreeing about it. Payable lines are excluded -- they are the absence of a
        finding, not a finding.
        """
        items: list[Finding] = [
            f for f in self.findings if f.classification != "PAYABLE"
        ]
        return sorted(
            items + list(self.document_gaps) + list(self.consistency_flags),
            key=lambda f: (SEVERITY_ORDER[f.severity], -(f.impact or Decimal("0"))),
        )

    # computed_field, not a plain property: this has to survive serialisation. The UI
    # talks to the engine over HTTP, and a property is invisible to model_dump -- so
    # the single most important thing on the screen would not have crossed the boundary.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def verdict(self) -> Literal["CLEAN", "NEEDS_ATTENTION", "CANNOT_VERIFY"]:
        """The one thing the user needs to read at a glance.

        Order matters. `CANNOT_VERIFY` wins over `NEEDS_ATTENTION` because a partial
        check that found two problems has not established there are only two; and it
        wins over `CLEAN` for the same reason, which is the case that used to be shown
        as a pair of green success boxes regardless of how much of the bill was legible.
        """
        if self.caveats or not self.verify_passed:
            return "CANNOT_VERIFY"
        if self.all_findings:
            return "NEEDS_ATTENTION"
        return "CLEAN"


# --- agent state ----------------------------------------------------------


class ClaimState(BaseModel):
    """Flows through the graph. Each node returns a dict of updates."""

    packet: ClaimPacket
    findings: list[ItemFinding] = Field(default_factory=list)
    profiles: dict[str, WaterfallResult] = Field(default_factory=dict)
    document_gaps: list[DocumentGap] = Field(default_factory=list)
    consistency_flags: list[ConsistencyFlag] = Field(default_factory=list)
    narrative: str = ""
    action_list: list[str] = Field(default_factory=list)
    ai_used: bool = False
    errors: list[str] = Field(default_factory=list)
    # The exact fact set the narrative was generated from. Kept so the judge can be
    # scored against the same evidence the writer had -- scoring against a smaller
    # fact set just measures what you withheld.
    facts_json: str = ""

    # Verifier state. `repair_count` bounds the cycle so a model that cannot be
    # talked into consistency degrades instead of looping forever.
    verify_passed: bool = True
    verify_problems: list[str] = Field(default_factory=list)
    repair_count: int = 0
