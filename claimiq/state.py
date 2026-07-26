"""Every Pydantic model in the system, plus the agent state that flows between nodes.

Money is `Decimal` throughout. Never float -- a claim auditor that is off by a paisa
because of binary floating point is worse than useless.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

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

Severity = Literal["BLOCKER", "QUERY_LIKELY", "ADVISORY"]

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


class ItemFinding(BaseModel):
    line_no: int
    description: str
    head: Head
    amount: Decimal
    classification: Classification
    bearer: Bearer
    deducted_amount: Decimal
    reason: str
    confidence: float = 1.0
    # The retrieved corpus chunk this determination was grounded in. Required for
    # `llm` findings and enforced by the verifier -- the point of the rule is to stop
    # the model asserting things it cannot point at. Keyword findings legitimately
    # have none: the hand-written table is itself auditable code.
    cited_chunk_id: str | None = None
    source: Literal["keyword", "retrieval", "llm"] = "keyword"


class PolicyDeduction(BaseModel):
    step: str
    basis: str
    amount: Decimal


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


class DocumentGap(BaseModel):
    document_id: str
    name: str
    severity: Severity
    reason: str


class ConsistencyFlag(BaseModel):
    check_id: str
    severity: Severity
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
    corpus_version: str = "unversioned"
    verify_passed: bool = True
    verify_problems: list[str] = Field(default_factory=list)
    repair_count: int = 0

    @property
    def settlement_low(self) -> Decimal:
        return min(w.projected_settlement for w in self.profiles.values())

    @property
    def settlement_high(self) -> Decimal:
        return max(w.projected_settlement for w in self.profiles.values())

    @property
    def typical(self) -> WaterfallResult:
        return self.profiles[self.typical_profile]


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
