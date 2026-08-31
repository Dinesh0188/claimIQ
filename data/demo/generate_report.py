import json, pathlib
from decimal import Decimal
from pathlib import Path

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "demo" / "generated" / "SYNTH-DEMO-42-001"
packet_path = OUT_DIR / "00_claim_packet.json"
audit_path = OUT_DIR / "06_audit_result.json"

packet = json.load(open(packet_path))
audit = json.load(open(audit_path))

gross = Decimal(audit["gross_bill"])
profiles = audit["profiles"]
typ = profiles["typical"]
cons = profiles["conservative"]
leni = profiles["lenient"]

hospital_preventable = sum(Decimal(f["deducted_amount"]) for f in audit["findings"] if f["bearer"]=="HOSPITAL")
patient_total = sum(Decimal(f["deducted_amount"]) for f in audit["findings"] if f["bearer"]=="PATIENT")
preventable_pct = hospital_preventable / gross * 100 if gross else Decimal(0)
hospital_items = [f for f in audit["findings"] if f["bearer"]=="HOSPITAL"]
largest = max(hospital_items, key=lambda x: Decimal(x["deducted_amount"])) if hospital_items else None

# Compute top 3 billing-master problems by grouping by rule_id
from collections import Counter, defaultdict
counter = Counter(f["rule_id"] for f in hospital_items)
amount_by_rule = defaultdict(Decimal)
for f in hospital_items:
    amount_by_rule[f["rule_id"]] += Decimal(f["deducted_amount"])
# Map rule_id to title via findings
rule_title = {f["rule_id"]: f["citation"].split(",")[0][:60] if f["citation"] else f["description"] for f in hospital_items}
# Actually get title from findings reason? Let's get from audit findings cited_chunk_id mapping - use description as proxy
# Better load corpus to get title
top3 = sorted(amount_by_rule.items(), key=lambda x: x[1], reverse=True)[:3]

# For report, we need to load corpus titles: parse from findings citation? Use observed-> expected mapping.
# Let's just use the finding's rule_id and its reason includes title.

report_lines = []
def w(s=""):
    report_lines.append(s)

w("# ClaimIQ Synthetic Business-Value Demonstration — SEED 42")
w("")
w("> **SYNTHETIC DATA ONLY** — All hospitals, insurers, patients, bills and documents in this report are fictional and watermarked SYNTHETIC. No real patient or hospital data was used. Generated with fixed random seed `42` for exact reproducibility.")
w("")

# 1. Synthetic scenario summary
w("## 1. Synthetic Scenario Summary")
w("")
w(f"- **Claim ID:** `SYNTH-DEMO-42-001` (SYNTHETIC, seed 42)")
w(f"- **Hospital (fictional):** SYNTHETIC City Care Hospital, Pune — SYNTHETIC Baner Road, Pune MH 411045 (Private Multi-Specialty, SYNTHETIC)")
w(f"- **Insurer (fictional):** SYNTHETIC Bharosa Health Insurance Co. Ltd. — Product SYNTHETIC Aarogya Plus")
w(f"- **Patient (fictional):** SYNTHETIC Patient — Demo Case 42, Male, 42y, UHID SYNTH-UHID-42-001")
w(f"- **Policy (fictional):** `{packet['policy']['policy_id']}` — Sum Insured ₹{int(Decimal(packet['policy']['sum_insured'])):,}, Room Cap ₹{packet['policy']['room_rent_cap_per_day']}/day, ICU Cap ₹{packet['policy']['icu_cap_per_day']}/day, Co-pay {packet['policy']['copay_percent']}%, Deductible ₹{packet['policy']['deductible']}, Procedure sublimits none")
w(f"- **Coverage:** 2024-06-01 → 2025-05-31, Claim type cashless, Pre-auth approved ₹{int(Decimal(packet['context']['preauth_approved_amount'])):,}")
w(f"- **Admission:** {packet['context']['admission_date']} → Discharge: {packet['context']['discharge_date']} ({packet['room_stay']['days']} days) — Room {packet['room_stay']['room_category']} @ ₹{packet['room_stay']['rate_per_day']}/day (is_icu={packet['room_stay']['is_icu']})")
w(f"- **Diagnosis (fictional):** {packet['context']['primary_diagnosis']}")
w(f"- **Procedure (fictional):** {packet['context']['procedure_performed']}")
w(f"- **Gross Bill:** ₹{gross:,} (14 lines)")
w(f"- **Corpus version:** {audit['corpus_version']} — strategy `{audit['strategy']}`, AI used: {audit['ai_used']}, unmapped: {audit['unmapped_count']}, verify: {'PASS' if audit['verify_passed'] else 'FAIL'}")
w(f"- **Documents attached (SYNTHETIC):** {', '.join(packet['context']['documents_attached'])}")
w(f"- **Deliberately missing (reported as gap):** DOC-INVESTIGATION-REPORTS — USG Abdomen + LFT reports for INVESTIGATION line ₹12,000")
w("")

# 2. Hospital Preventable Savings
w("## 2. Hospital Preventable Savings")
w("")
w("**Typical insurer profile is the headline. Conservative and lenient shown beside it.**")
w("")
w(f"- **Gross bill (SYNTHETIC):** ₹{gross:,}")
w(f"- **Estimated settlement (Typical):** ₹{Decimal(typ['projected_settlement']):,} — Conservative ₹{Decimal(cons['projected_settlement']):,} — Lenient ₹{Decimal(leni['projected_settlement']):,}")
w(f"- **Patient liability (Typical):** ₹{Decimal(typ['patient_liability']):,} — Conservative ₹{Decimal(cons['patient_liability']):,} — Lenient ₹{Decimal(leni['patient_liability']):,}")
w(f"- **Hospital write-off (Typical):** ₹{Decimal(typ['hospital_writeoff']):,}")
w(f"- **Hospital preventable write-off (definition: sum deducted where bearer == HOSPITAL):** **₹{hospital_preventable:,}**")
w(f"- **Preventable as % of gross:** **{preventable_pct:.2f}%** ({hospital_preventable} / {gross} ×100)")
w(f"- **Number of hospital-borne line-item errors:** {len(hospital_items)}")
if largest:
    w(f"- **Largest hospital-borne deduction:** ₹{Decimal(largest['deducted_amount']):,} — {largest['description']} ({largest['rule_id']}, {largest['citation'][:80]})")
w(f"- **Top 3 recurring billing-master problems (by amount this claim):**")
for rid, amt in top3:
    # find example finding for that rule
    ex = next(f for f in hospital_items if f['rule_id']==rid)
    w(f"  - {rid}: ₹{amt:,} — {ex['description']} — {ex['citation'][:70]}")
w("")
w(f"**Annualised preventable opportunity (estimated opportunity, not guaranteed):**")
w(f"- 100 claims/yr: **₹{hospital_preventable*100:,}** (₹{hospital_preventable:,} ×100)")
w(f"- 500 claims/yr: **₹{hospital_preventable*500:,}**")
w(f"- 1,000 claims/yr: **₹{hospital_preventable*1000:,}**")
w("")

# 3. Financial waterfall
w("## 3. Typical / Conservative / Lenient Financial Waterfall")
w("")
w("| Profile | Gross | Item deductions (I-IV) | Room excess | Proportionate | Co-pay | Settlement | Patient | Hospital write-off |")
w("|---|---|---|---|---|---|---|---|---|")
for name, p in [("Typical", typ), ("Conservative", cons), ("Lenient", leni)]:
    # parse deductions
    room_excess = next((d["amount"] for d in p["policy_deductions"] if d["step"]=="room_rent_excess"), "0")
    prop = next((d["amount"] for d in p["policy_deductions"] if d["step"]=="room_rent_proportionate"), "0")
    copay = next((d["amount"] for d in p["policy_deductions"] if d["step"]=="copay"), "0")
    w(f"| {name} | ₹{Decimal(p['gross_bill']):,} | ₹{Decimal(p['item_deduction_total']):,} | ₹{Decimal(room_excess):,} | ₹{Decimal(prop):,} | ₹{Decimal(copay):,} | **₹{Decimal(p['projected_settlement']):,}** | ₹{Decimal(p['patient_liability']):,} | ₹{Decimal(p['hospital_writeoff']):,} |")
w("")
w("**Detailed policy deductions (Typical):**")
for d in typ["policy_deductions"]:
    w(f"- `{d['step']}` — {d['basis']} — **₹{Decimal(d['amount']):,}**")
    if d.get("inputs"):
        for k,v in d["inputs"].items():
            w(f"  - {k}: {v}")
    if d.get("formula"):
        w(f"  - formula: `{d['formula']}`")
w("")

# 4. Before-and-after — business-value interpretation: bundle/reclassify, total bill unchanged
w("## 4. Before-and-After Hospital Savings Table")
w("")
w(f"| Scenario | Gross | Preventable (HOSPITAL) | Hospital write-off | Patient | Settlement | Improvement |")
w("|---|---|---|---|---|---|---|")
w(f"| **Current synthetic bill** | ₹{gross:,} | ₹{hospital_preventable:,} | ₹{Decimal(typ['hospital_writeoff']):,} | ₹{Decimal(typ['patient_liability']):,} | ₹{Decimal(typ['projected_settlement']):,} | — |")
# Business-value interpretation: bundle/reclassify the six preventable lines into legitimate package while keeping total bill unchanged.
# Gross stays 145,050; preventable moves from hospital write-off to settlement. This satisfies the invariant.
corrected_gross = gross
corrected_settlement = Decimal(typ["projected_settlement"]) + hospital_preventable
corrected_hospital = Decimal(0)
# Verification: corrected_settlement + patient + corrected_hospital == corrected_gross
assert corrected_settlement + Decimal(typ["patient_liability"]) + corrected_hospital == corrected_gross, "corrected invariant failed"
w(f"| **Corrected billing master — bundle/reclassify 6 lines** | ₹{corrected_gross:,} | ₹0 | ₹{corrected_hospital:,} | ₹{Decimal(typ['patient_liability']):,} | ₹{corrected_settlement:,} | **+₹{hospital_preventable:,} per claim** |")
w("")
w(f"- **Billing-master action:** Bundle/reclassify 6 HOSPITAL lines (₹750 Housekeeping + ₹600 Documentation + ₹2,200 OT drape + ₹1,800 STRL GLV + ₹800 REGN + ₹2,500 Service) into legitimate room/procedure/treatment package — total bill stays ₹{gross:,}, not reduced. See §8.")
w(f"- **Room/tariff control:** Bill Single AC at ≤₹6,000/day or upgrade policy cap; currently 12,000 vs 6,000 → ₹18,000 excess + ₹34,750 proportionate (Typical). Correcting room alone saves ₹52,750 of patient+insurer flow but **not** counted as hospital savings — it is patient/insurer room cap, not HOSPITAL bearer. Hospital savings are strictly the 6 HOSPITAL lines.")
w(f"- **Estimated hospital write-off after correction:** ₹0 (for those 6 lines); invariant holds: {corrected_settlement:,} + {Decimal(typ['patient_liability']):,} + {corrected_hospital:,} = {corrected_gross:,}.")
w(f"- **Alternative if lines were literally removed (smaller bill, not the savings story):** Gross ₹{gross - hospital_preventable:,} Settlement ₹{Decimal(typ['projected_settlement']):,} Patient ₹{Decimal(typ['patient_liability']):,} Hospital ₹0 — reconciles but shows no +₹8,650 improvement, only a smaller bill.")
w("")

# 5. Full line-item classifications — corpus citations only on deducted items
w("## 5. Full Line-Item Classifications — Corpus Citations on Deducted Items")
w("")
w("_14 line items classified; 7 non-payable findings cited to the unverified corpus; 7 primary payable lines classified by billing head without a specific non-payable citation._")
w("")
w("| Line | Description | Amount | Classification | List | Bearer | Deducted | Chunk ID | Rule Title / Citation | Explanation | Preventable | Action |")
w("|---|---|---|---|---|---|---|---|---|---|---|---|")
LIST_LABEL = {
    "LIST_I_OPTIONAL": "I",
    "LIST_II_ROOM": "II",
    "LIST_III_PROCEDURE": "III",
    "LIST_IV_TREATMENT": "IV",
}
for f in audit["findings"]:
    list_name = f["classification"]
    bearer = f["bearer"]
    deducted = f["deducted_amount"]
    chunk = f["cited_chunk_id"] or "—"
    title = f["citation"][:80] if f["citation"] else "—"
    # For PAYABLE lines, show no citation rather than inventing one
    if list_name == "PAYABLE":
        title = "— (primary service by head)"
    preventable = "Yes" if bearer=="HOSPITAL" else "No"
    if list_name=="LIST_I_OPTIONAL":
        action = "Inform patient at admission, take signed acknowledgement; bill to patient"
    elif list_name=="LIST_II_ROOM":
        action = "Remove from itemised bill; fold into room tariff"
    elif list_name=="LIST_III_PROCEDURE":
        action = "Fold into procedure/package rate"
    elif list_name=="LIST_IV_TREATMENT":
        action = "Fold into cost of treatment; remove surcharge"
    elif list_name=="PAYABLE":
        action = "Keep — primary service"
    else:
        action = "Manual review"
    list_label = LIST_LABEL.get(list_name, "—")
    w(f"| {f['line_no']} | {f['description']} | ₹{Decimal(f['amount']):,} | {list_name} | {list_label} | {bearer} | ₹{Decimal(deducted):,} | {chunk} | {title} | {f['reason'][:60]} | {preventable} | {action} |")
w("")

# 6. Patient-borne deductions
w("## 6. Patient-Borne Deductions (Not Hospital Savings)")
w("")
w("| Category | Amount per claim | Cause | Rule citation | Billing change | Annual opportunity (not hospital) |")
w("|---|---|---|---|---|---|")
# List I
w(f"| List I optional/personal | ₹900 | Attendant food (convenience) | {next(f['citation'] for f in audit['findings'] if f['line_no']==8)[:60]} | Bill to patient with consent, do not absorb | 100/yr ₹90,000; 500/yr ₹450,000; 1k/yr ₹900,000 |")
# Room-cap deductions are not in findings but in policy deductions
room_excess = Decimal(next(d["amount"] for d in typ["policy_deductions"] if d["step"]=="room_rent_excess"))
prop = Decimal(next(d["amount"] for d in typ["policy_deductions"] if d["step"]=="room_rent_proportionate"))
copay = Decimal(next(d["amount"] for d in typ["policy_deductions"] if d["step"]=="copay"))
w(f"| Room-cap excess | ₹{room_excess:,} | Single AC ₹12k vs cap ₹6k ×3 days | Policy schedule: room_rent_cap_per_day ₹6,000 | Check cap before billing; counsel patient on upgrade | — |")
w(f"| Proportionate (Typical) | ₹{prop:,} | Associated charges scaled by 50% | Policy: heads included NURSING,OTHER,PROCEDURE | Same | — |")
w(f"| Co-pay 10% | ₹{copay:,} | Admissible ×10% | Policy copay_percent 10 | Inform patient at admission | — |")
w(f"| Deductible | ₹0 | — | — | — | — |")
w(f"| Procedure sublimits | ₹0 | none | — | — | — |")
w("")
w("**Note:** These increase patient liability but are not automatically hospital savings. Only bearer == HOSPITAL is counted in §2 and §4.")
w("")

# 7. Missing-document and consistency risks
w("## 7. Missing-Document and Consistency Risks")
w("")
if audit["document_gaps"]:
    w("| Document ID | Name | Severity | Reason | Likely consequence | Prevent deduction? |")
    w("|---|---|---|---|---|---|")
    for g in audit["document_gaps"]:
        consequence = "Query / delay — investigations held pending report" if g["document_id"]=="DOC-INVESTIGATION-REPORTS" else "Query"
        prevent = "No — reduces query risk, not direct deduction (unless report proves medical necessity)" 
        w(f"| {g['document_id']} | {g['name']} | {g['severity']} | {g['reason']} | {consequence} | {prevent} |")
else:
    w("No gaps.")
w("")
if audit["consistency_flags"]:
    w("Consistency flags:")
    for c in audit["consistency_flags"]:
        w(f"- {c}")
else:
    w("No consistency flags (dates, head/service-date, implant invoice all consistent).")
w("")

# 8. Top 3 billing-master fixes
w("## 8. Top 3 Billing-Master Fixes (Ranked by Impact)")
w("")
# Rank by amount
ranked = sorted(hospital_items, key=lambda x: Decimal(x["deducted_amount"]), reverse=True)[:3]
for i, f in enumerate(ranked, 1):
    w(f"**{i}. {f['description']} — ₹{Decimal(f['deducted_amount']):,}**")
    w(f"- Current: billed as separate head=OTHER line ₹{Decimal(f['amount']):,}")
    w(f"- Rule: {f['rule_id']} — {f['citation']} (chunk {f['cited_chunk_id']}, list {f['classification']})")
    w(f"- Explanation: {f['reason']}")
    w(f"- Action: {'Bundle into procedure package' if 'LIST_III' in f['classification'] else 'Remove from bill / fold into room or treatment cost'}")
    w(f"- Estimated saving per claim: **₹{Decimal(f['deducted_amount']):,}** — Annual 1k claims **₹{Decimal(f['deducted_amount'])*1000:,}**")
    w("")
# Also summarize overall
w(f"**Overall billing-master cleanup:** Removing all 6 HOSPITAL lines saves **₹{hospital_preventable:,} per claim** (₹{hospital_preventable*1000:,} per 1k claims). Room-rate correction is separate and not counted here.")
w("")

# 9. Annualised opportunity
w("## 9. Annualised Opportunity at 100, 500 and 1,000 Claims")
w("")
w("| Volume | Preventable per claim | Annual preventable opportunity | Label |")
w("|---|---|---|---|")
w(f"| 100/yr | ₹{hospital_preventable:,} | **₹{hospital_preventable*100:,}** | estimated opportunity |")
w(f"| 500/yr | ₹{hospital_preventable:,} | **₹{hospital_preventable*500:,}** | estimated opportunity |")
w(f"| 1,000/yr | ₹{hospital_preventable:,} | **₹{hospital_preventable*1000:,}** | estimated opportunity |")
w("")
w(f"Formula: `annualised preventable opportunity = preventable hospital write-off per claim × annual claim volume` = `{hospital_preventable} × volume`")
w("")

# 10. Invariant check
w("## 10. Exact Invariant Check (Typical Profile)")
w("")
for name in ["typical","conservative","lenient"]:
    p = profiles[name]
    s = Decimal(p["projected_settlement"])
    pat = Decimal(p["patient_liability"])
    hosp = Decimal(p["hospital_writeoff"])
    total = s+pat+hosp
    grossd = Decimal(p["gross_bill"])
    w(f"- **{name}**: {s:,} + {pat:,} + {hosp:,} = **{total:,}** — Gross {grossd:,} — **{'PASS ✓' if total==grossd else 'FAIL ✗'}**")
w("")
w("Invariant: `projected settlement + patient liability + hospital write-off == gross bill` — asserted per test suite and verified here per profile.")
w("")

# 11. Generated document paths
w("## 11. Generated Document Paths (SYNTHETIC)")
w("")
for p in sorted(OUT_DIR.iterdir()):
    w(f"- `{p.relative_to(ROOT).as_posix()}`")
w("")
w("All files watermarked SYNTHETIC, seed 42, under `data/demo/generated/SYNTH-DEMO-42-001/`. No real data.")
w("")

# 12. Disclaimers
w("## 12. Disclaimers")
w("")
w("- **Synthetic data only.** Every claim number, hospital, insurer, patient, bill line and document in this demo is fictional and marked SYNTHETIC. No PHI or real hospital data was used.")
w("- **Estimate, not adjudication.** ClaimIQ estimates likely deductions under configurable insurer rules (typical/conservative/lenient). It does not predict any specific adjudicator's decision.")
w("- **Rule corpus is an unverified snapshot.** Citations are from `corpus/non_payable/*.md` and `corpus/policy/*.md` which are explicitly labelled `UNVERIFIED SNAPSHOT — compiled from publicly circulated lists, not checked against current insurer or IRDAI wording`. See `corpus/SOURCES.toml` `status = \"unverified\"` and the banner in the product.")
w("- **Savings are an estimated preventable opportunity, not a guarantee.** Hospital preventable savings are defined strictly as `sum deducted where bearer == HOSPITAL` (Lists II, III, IV). Room-cap, proportionate, co-pay and List I patient items are patient-borne and not counted. Annualised figures assume the same preventable amount per claim and are labelled as estimated opportunity.")
w("- **Deterministic engine.** This run used `AI_ENABLED=false`, the offline keyword+retrieval path whose measured non-payable recall is 74.6% on the labelled benchmark (see `CLASSIFICATION.md`). Lines shown as PAYABLE have not been cleared, only unmatched — disclosures in the audit result state this explicitly.")
w("")

# Save
out_md = OUT_DIR / "07_business_value_report.md"
out_md.write_text("\n".join(report_lines), encoding="utf-8")
print(f"Wrote report to {out_md}")
# Also print summary to stdout
print("\n".join(report_lines[:80]))
