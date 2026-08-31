#!/usr/bin/env python
"""Synthetic business-value demo with seed 42 — all data fictional, SYNTHETIC watermarked."""

import json
import random
from pathlib import Path
from decimal import Decimal
from datetime import date, timedelta
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Ensure deterministic, no LLM
import os
os.environ["AI_ENABLED"] = "false"

from claimiq.state import ClaimPacket, BillLineItem, RoomStay, PolicyTerms, ClaimContext
from claimiq.graph import audit

SEED = 42
rng = random.Random(SEED)

# --- Synthetic scenario constants (clearly fictional) ---
CLAIM_ID = "SYNTH-DEMO-42-001"
OUT_DIR = ROOT / "data" / "demo" / "generated" / CLAIM_ID
OUT_DIR.mkdir(parents=True, exist_ok=True)

HOSPITAL = {
    "name": "SYNTHETIC City Care Hospital, Pune",
    "address": "SYNTHETIC, Baner Road, Pune, MH 411045",
    "type": "Private Multi-Specialty (SYNTHETIC)",
}

INSURER = {
    "name": "SYNTHETIC Bharosa Health Insurance Co. Ltd.",
    "product": "SYNTHETIC Aarogya Plus",
}

PATIENT = {
    "name": "SYNTHETIC Patient — Demo Case 42",
    "age": 42,
    "gender": "Male",
    "uhid": "SYNTH-UHID-42-001",
}

# Policy terms
POLICY_ID = "SYNTH-POL-42-001"
SUM_INSURED = Decimal("500000")
BALANCE = Decimal("500000")
ROOM_CAP = Decimal("6000")
ICU_CAP = Decimal("12000")
COPAY = Decimal("10")
DEDUCTIBLE = Decimal("0")
ROOM_CATEGORY = "Single AC Room"
ROOM_RATE = Decimal("12000")
DAYS = 3
ADMISSION = date(2025, 3, 11)
DISCHARGE = ADMISSION + timedelta(days=DAYS)
DIAGNOSIS = "Cholelithiasis"
PROCEDURE = "Laparoscopic cholecystectomy"

# Build line items — intentionally mixed for business value story
# Heads chosen so that deterministic classifier (head==OTHER) will correctly identify non-payable items
# Payable items use primary heads and will not be flagged even if description contains alias substrings
raw_items = [
    # payable primary services
    {"line_no": 1, "description": "Room charges - Single AC Room", "head": "ROOM", "quantity": "3", "unit_rate": "12000", "amount": "36000"},
    {"line_no": 2, "description": "Nursing charges", "head": "NURSING", "quantity": "3", "unit_rate": "1500", "amount": "4500"},
    {"line_no": 3, "description": "Surgeon fee - Laparoscopic cholecystectomy", "head": "PROCEDURE", "quantity": "1", "unit_rate": "35000", "amount": "35000"},
    {"line_no": 4, "description": "OT charges", "head": "PROCEDURE", "quantity": "1", "unit_rate": "18000", "amount": "18000"},
    {"line_no": 5, "description": "Anaesthetist charges", "head": "PROCEDURE", "quantity": "1", "unit_rate": "12000", "amount": "12000"},
    {"line_no": 6, "description": "Pharmacy and consumables", "head": "PHARMACY", "quantity": "1", "unit_rate": "18000", "amount": "18000"},
    {"line_no": 7, "description": "Radiology and laboratory - USG Abdomen + LFT", "head": "INVESTIGATION", "quantity": "1", "unit_rate": "12000", "amount": "12000"},
    # List I — PATIENT borne
    {"line_no": 8, "description": "Attendant food charges", "head": "OTHER", "quantity": "3", "unit_rate": "300", "amount": "900"},
    # List II — HOSPITAL (room)
    {"line_no": 9, "description": "Housekeeping charges", "head": "OTHER", "quantity": "1", "unit_rate": "750", "amount": "750"},
    {"line_no": 10, "description": "Documentation charges", "head": "OTHER", "quantity": "1", "unit_rate": "600", "amount": "600"},
    # List III — HOSPITAL (procedure)
    {"line_no": 11, "description": "OT drape sterile 4NOS", "head": "OTHER", "quantity": "1", "unit_rate": "2200", "amount": "2200"},
    {"line_no": 12, "description": "STRL GLV 7.5", "head": "OTHER", "quantity": "6", "unit_rate": "300", "amount": "1800"},
    # List IV — HOSPITAL (treatment)
    {"line_no": 13, "description": "REGN CHARGES", "head": "OTHER", "quantity": "1", "unit_rate": "800", "amount": "800"},
    {"line_no": 14, "description": "Service charges 5PCT", "head": "OTHER", "quantity": "1", "unit_rate": "2500", "amount": "2500"},
]

gross = sum(Decimal(x["amount"]) for x in raw_items)
print(f"Gross bill computed: {gross}")

# Preauth just under gross to avoid enhancement gap (must be < gross*1.1 to avoid, so set to gross)
preauth = gross  # exactly gross, so gross == preauth => not >1.1

# Documents attached — deliberately missing investigation reports
attached = [
    "DOC-CLAIM-FORM",
    "DOC-DISCHARGE-SUMMARY",
    "DOC-FINAL-BILL",
    "DOC-ID-PROOF",
    "DOC-INDOOR-PAPERS",
    "DOC-OT-NOTES",
    "DOC-PHARMACY-BILLS",
]
# Missing: DOC-INVESTIGATION-REPORTS (will be reported as gap)

packet_dict = {
    "claim_id": CLAIM_ID,
    "_watermark": "SYNTHETIC - NOT REAL PATIENT DATA - SEED 42",
    "context": {
        "claim_type": "cashless",
        "admission_date": ADMISSION.isoformat(),
        "discharge_date": DISCHARGE.isoformat(),
        "primary_diagnosis": DIAGNOSIS,
        "procedure_performed": PROCEDURE,
        "is_accident": False,
        "is_maternity": False,
        "involves_implant": False,
        "is_ped_related": False,
        "preauth_approved_amount": str(preauth),
        "documents_attached": attached,
    },
    "policy": {
        "policy_id": POLICY_ID,
        "sum_insured": str(SUM_INSURED),
        "balance_sum_insured": str(BALANCE),
        "room_rent_cap_per_day": str(ROOM_CAP),
        "icu_cap_per_day": str(ICU_CAP),
        "copay_percent": str(COPAY),
        "deductible": str(DEDUCTIBLE),
        "procedure_sublimits": {},
        "policy_inception_date": "2024-06-01",
    },
    "room_stay": {
        "room_category": ROOM_CATEGORY,
        "rate_per_day": str(ROOM_RATE),
        "days": DAYS,
        "is_icu": False,
    },
    "line_items": raw_items,
}

packet = ClaimPacket.model_validate(packet_dict)

# --- Save synthetic documents ---
# 1. Policy schedule
policy_doc = {
    "_watermark": "SYNTHETIC - NOT REAL POLICY",
    "policy_id": POLICY_ID,
    "insurer": INSURER["name"],
    "product": INSURER["product"],
    "sum_insured": str(SUM_INSURED),
    "room_rent_cap_per_day": str(ROOM_CAP),
    "icu_cap_per_day": str(ICU_CAP),
    "copay_percent": str(COPAY),
    "deductible": str(DEDUCTIBLE),
    "procedure_sublimits": {},
    "claim_type": "cashless",
    "coverage": {"inception": "2024-06-01", "expiry": "2025-05-31"},
    "hospital": HOSPITAL["name"],
    "note": "SYNTHETIC schedule generated with seed 42 for demo only. Unverified snapshot.",
}
(OUT_DIR / "01_policy_schedule.json").write_text(json.dumps(policy_doc, indent=2), encoding="utf-8")

# 2. Itemised hospital bill (as JSON and as simple PDF-like text)
bill_doc = {
    "_watermark": "SYNTHETIC - NOT REAL BILL",
    "hospital": HOSPITAL,
    "patient": PATIENT,
    "claim_id": CLAIM_ID,
    "admission_date": ADMISSION.isoformat(),
    "discharge_date": DISCHARGE.isoformat(),
    "line_items": raw_items,
    "gross_bill": str(gross),
}
(OUT_DIR / "02_hospital_bill.json").write_text(json.dumps(bill_doc, indent=2), encoding="utf-8")

# Also write a human-readable text version
bill_text_lines = [f"SYNTHETIC HOSPITAL BILL — {HOSPITAL['name']}", f"Claim: {CLAIM_ID}  Patient: {PATIENT['name']}  SYNTHETIC", "SNo | Description | Head | Qty | Rate | Amount"]
for it in raw_items:
    bill_text_lines.append(f"{it['line_no']:2} | {it['description'][:36]:36} | {it['head']:12} | {it['quantity']:>3} | {it['unit_rate']:>6} | {it['amount']:>6}")
bill_text_lines.append(f"GROSS TOTAL: Rs {gross:,}")
(OUT_DIR / "02_hospital_bill.txt").write_text("\n".join(bill_text_lines), encoding="utf-8")

# 3. Discharge summary
discharge_doc = {
    "_watermark": "SYNTHETIC - NOT REAL CLINICAL RECORD",
    "hospital": HOSPITAL["name"],
    "patient": PATIENT["name"],
    "admission_date": ADMISSION.isoformat(),
    "discharge_date": DISCHARGE.isoformat(),
    "diagnosis": DIAGNOSIS,
    "procedure_performed": PROCEDURE,
    "room_category": ROOM_CATEGORY,
    "length_of_stay_days": DAYS,
    "consultant": "SYNTHETIC Dr. Mehta, MS Surgery (SYNTHETIC)",
    "condition_on_discharge": "Stable, afebrile, wound healthy",
}
(OUT_DIR / "03_discharge_summary.json").write_text(json.dumps(discharge_doc, indent=2), encoding="utf-8")

# 4. Supporting clinical documents
investigation_doc = {
    "_watermark": "SYNTHETIC - Investigation report WOULD BE HERE but deliberately missing for demo",
    "note": "This file intentionally NOT created as attached document. The audit should report DOC-INVESTIGATION-REPORTS as a gap.",
}
# We do NOT create the investigation report file that would be attached; instead we create a placeholder that explains the gap
(OUT_DIR / "04_investigation_report_MISSING_NOTE.json").write_text(json.dumps(investigation_doc, indent=2), encoding="utf-8")

ot_note = {
    "_watermark": "SYNTHETIC - NOT REAL OT NOTE",
    "claim_id": CLAIM_ID,
    "procedure": PROCEDURE,
    "date": ADMISSION.isoformat(),
    "surgeon": "SYNTHETIC Dr. Mehta",
    "anaesthetist": "SYNTHETIC Dr. Rao",
    "findings": "Cholelithiasis with chronic cholecystitis. Laparoscopic cholecystectomy completed. No intra-op complications.",
    "specimen": "Gallbladder sent for histopathology",
}
(OUT_DIR / "04b_ot_notes.json").write_text(json.dumps(ot_note, indent=2), encoding="utf-8")

pharmacy_note = {
    "_watermark": "SYNTHETIC - pharmacy bills would be attached",
    "claim_id": CLAIM_ID,
    "note": "SYNTHETIC pharmacy bills with prescriptions — 1 file, Rs 18,000, matching PHARMACY line.",
}
(OUT_DIR / "04c_pharmacy_bills.json").write_text(json.dumps(pharmacy_note, indent=2), encoding="utf-8")

# 5. Deliberately missing document record (not a file that is silently invented)
missing_doc_record = {
    "_watermark": "SYNTHETIC - DELIBERATELY MISSING",
    "missing_document_id": "DOC-INVESTIGATION-REPORTS",
    "name": "Investigation reports matching billed diagnostics",
    "expected": "USG Abdomen + LFT reports corresponding to INVESTIGATION line Rs 12,000",
    "attached_documents": attached,
    "reason": "Intentionally omitted to demonstrate document-gap detection. Billed investigations without reports are held pending query.",
}
(OUT_DIR / "05_missing_or_incomplete_document.json").write_text(json.dumps(missing_doc_record, indent=2), encoding="utf-8")

# Save the packet itself (what the audit actually consumes)
(OUT_DIR / "00_claim_packet.json").write_text(json.dumps(packet_dict, indent=2), encoding="utf-8")

print(f"Generated synthetic documents in {OUT_DIR}")
for p in sorted(OUT_DIR.iterdir()):
    print(f"  - {p.relative_to(ROOT)}")

# --- Run real ClaimIQ audit ---
print("\n--- Running real ClaimIQ audit (AI_ENABLED=false, deterministic) ---")
result = audit(packet, persist=True)

print(f"\nAudit result for {result.claim_id}")
print(f"Gross: {result.gross_bill}")
for name, w in result.profiles.items():
    print(f"  {name}: settlement {w.projected_settlement}  patient {w.patient_liability}  hospital {w.hospital_writeoff}")

# Persist verification
from claimiq import store
from claimiq.state import AuditResult

# Quick store check
try:
    detail = store.get_claim(result.claim_id)
    print(f"\nPersisted claim detail retrieved: {detail.claim_id} month {detail.month} gross {detail.gross_bill}")
except Exception as e:
    print(f"Store retrieval note: {e}")

# Save audit result JSON for reference
(OUT_DIR / "06_audit_result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
print(f"Saved audit result to {OUT_DIR / '06_audit_result.json'}")

# Quick invariant check for typical
typ = result.profiles["typical"]
invariant = typ.projected_settlement + typ.patient_liability + typ.hospital_writeoff
print(f"\nInvariant check (typical): {typ.projected_settlement} + {typ.patient_liability} + {typ.hospital_writeoff} = {invariant}  gross {result.gross_bill}  {'PASS' if invariant == result.gross_bill else 'FAIL'}")
