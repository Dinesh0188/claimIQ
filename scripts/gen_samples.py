"""Generate a synthetic claim portfolio.

    python scripts/gen_samples.py 300

Every file is watermarked SYNTHETIC. No real claim data was used anywhere in this
project, and the generator is the reason none was needed.
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "generated" / "portfolio"

CASES = [
    ("Triple vessel coronary artery disease", "Coronary artery bypass graft", 90000, 55000, True),
    ("Primary osteoarthritis of the knee", "Total knee replacement", 120000, 45000, True),
    ("Acute appendicitis", "Laparoscopic appendectomy", 35000, 15000, False),
    ("Cholelithiasis", "Laparoscopic cholecystectomy", 40000, 18000, False),
    ("Senile cataract", "Phacoemulsification with IOL", 22000, 9000, True),
    ("Inguinal hernia", "Open hernioplasty with mesh", 30000, 12000, True),
    ("Uterine fibroid", "Total abdominal hysterectomy", 55000, 22000, False),
    ("Renal calculus", "Percutaneous nephrolithotomy", 60000, 25000, False),
    ("Compound fracture of tibia", "Open reduction internal fixation", 45000, 20000, True),
    ("Acute myocardial infarction", "Primary angioplasty with stent", 85000, 40000, True),
    ("Chronic tonsillitis", "Tonsillectomy", 25000, 10000, False),
    ("Deviated nasal septum", "Septoplasty", 28000, 11000, False),
]

ROOMS = [
    ("General Ward", 2000), ("General Ward", 2500), ("Twin Sharing", 4000),
    ("Twin Sharing", 4500), ("Single AC", 7500), ("Single AC", 9000),
    ("Single AC", 12000), ("Deluxe", 15000),
]

# Non-payable items the generator sprinkles in, with their true list membership.
NON_PAYABLE = [
    ("ATTENDER FOOD CHRG", "OTHER", 200, 400),
    ("Telephone charges", "OTHER", 100, 600),
    ("Laundry charges", "OTHER", 200, 800),
    ("Toiletries kit", "OTHER", 150, 500),
    ("ADULT DIAPER LARGE 10S", "OTHER", 300, 900),
    ("TV RENT PER DAY", "OTHER", 100, 300),
    ("PATIENT GOWN DISPOSABLE", "OTHER", 100, 450),
    ("Slippers", "OTHER", 80, 200),
    ("TISSUE ROLL 2PLY", "OTHER", 80, 300),
    ("HAND WASH LIQUID 500ML", "OTHER", 200, 600),
    ("BEDPAN SS", "OTHER", 150, 300),
    ("LINEN CHARGES PER DAY", "OTHER", 180, 400),
    ("HK CHARGES", "OTHER", 250, 700),
    ("Admission kit", "OTHER", 300, 800),
    ("ID BAND WRIST", "OTHER", 50, 150),
    ("PULSE OXIMETER MONITORING", "OTHER", 300, 1200),
    ("IV INJ ADMIN CHARGES", "OTHER", 200, 900),
    ("Documentation charges", "OTHER", 300, 900),
    ("OT DRAPE STERILE 4NOS", "OTHER", 800, 2800),
    ("STRL GLV 7.5", "OTHER", 500, 2200),
    ("GAUZE SWAB 10X10", "OTHER", 400, 1500),
    ("COTTON ROLL 500GM", "OTHER", 300, 900),
    ("MICROPORE 2INCH", "OTHER", 200, 700),
    ("CSSD CHARGES", "OTHER", 1000, 3500),
    ("SURGICAL BLADE NO 15", "OTHER", 300, 1100),
    ("Tourniquet", "OTHER", 200, 600),
    ("REGN CHARGES", "OTHER", 300, 800),
    ("MRD CHARGES", "OTHER", 200, 600),
    ("SERVICE CHRG 5PCT", "OTHER", 800, 2500),
    ("ALCOHOL SWAB 100S", "OTHER", 150, 400),
    ("BETADINE 500ML", "OTHER", 250, 700),
    ("UROBAG 2000ML", "OTHER", 200, 500),
    ("MISC-CONS CHG", "OTHER", 500, 2000),
]

ALL_DOCS = [
    "DOC-CLAIM-FORM", "DOC-DISCHARGE-SUMMARY", "DOC-FINAL-BILL", "DOC-ID-PROOF",
    "DOC-INDOOR-PAPERS", "DOC-INVESTIGATION-REPORTS", "DOC-OT-NOTES",
    "DOC-PHARMACY-BILLS", "DOC-IMPLANT-INV", "DOC-MLC",
]


def money(low: int, high: int) -> str:
    return str(Decimal(random.randint(low, high)))


def make_claim(index: int, rng: random.Random) -> dict:
    diagnosis, procedure, surgeon_base, ot_base, has_implant = rng.choice(CASES)
    room_category, room_rate = rng.choice(ROOMS)
    days = rng.randint(1, 10)
    admission = date(2026, rng.randint(1, 7), rng.randint(1, 28))
    discharge = admission + timedelta(days=days)

    cap = rng.choice([4000, 5000, 6000, 6000, 8000, None])
    copay = rng.choice([0, 0, 0, 10, 10, 20])

    items = []
    n = 1

    def add(description, head, amount, quantity=1, rate=None):
        nonlocal n
        items.append(
            {
                "line_no": n,
                "description": description,
                "head": head,
                "quantity": str(quantity),
                "unit_rate": str(rate if rate is not None else amount),
                "amount": str(amount),
            }
        )
        n += 1

    add(f"Room charges - {room_category}", "ROOM", room_rate * days, days, room_rate)
    nursing = rng.randint(800, 2500)
    add("Nursing charges", "NURSING", nursing * days, days, nursing)
    add(f"Surgeon fee - {procedure}", "PROCEDURE", int(surgeon_base * rng.uniform(0.8, 1.3)))
    add("OT charges", "PROCEDURE", int(ot_base * rng.uniform(0.8, 1.3)))
    add("Anaesthetist charges", "PROCEDURE", rng.randint(8000, 30000))
    add("Radiology and laboratory", "INVESTIGATION", rng.randint(5000, 45000))
    add("Pharmacy and consumables", "PHARMACY", rng.randint(6000, 60000))
    add("Consultant visit charges", "CONSULTATION", rng.randint(600, 1500) * days)
    if has_implant:
        add("Implant", "IMPLANT", rng.randint(15000, 160000))

    for description, head, low, high in rng.sample(NON_PAYABLE, rng.randint(4, 14)):
        add(description, head, rng.randint(low, high))

    gross = sum(int(i["amount"]) for i in items)
    attached = rng.sample(ALL_DOCS, rng.randint(3, len(ALL_DOCS)))

    return {
        "claim_id": f"SYNTH-{index:04d}",
        "_watermark": "SYNTHETIC - NOT REAL PATIENT DATA",
        "context": {
            "claim_type": rng.choice(["cashless", "cashless", "reimbursement"]),
            "admission_date": admission.isoformat(),
            "discharge_date": discharge.isoformat(),
            "primary_diagnosis": diagnosis,
            "procedure_performed": procedure,
            "is_accident": "fracture" in diagnosis.lower(),
            "is_maternity": False,
            "involves_implant": has_implant,
            "is_ped_related": rng.random() < 0.15,
            "preauth_approved_amount": str(int(gross * rng.uniform(0.7, 1.2))),
            "documents_attached": attached,
        },
        "policy": {
            "policy_id": f"SYNTH-POL-{rng.randint(1000, 9999)}",
            "sum_insured": str(rng.choice([300000, 500000, 500000, 800000, 1000000])),
            "balance_sum_insured": str(rng.choice([300000, 500000, 500000, 800000, 1000000])),
            "room_rent_cap_per_day": str(cap) if cap else None,
            "icu_cap_per_day": str(cap * 2) if cap else None,
            "copay_percent": str(copay),
            "deductible": "0",
            "procedure_sublimits": {},
            "policy_inception_date": "2020-06-01",
        },
        "room_stay": {
            "room_category": room_category,
            "rate_per_day": str(room_rate),
            "days": days,
            "is_icu": False,
        },
        "line_items": items,
    }


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    rng = random.Random(20260726)  # fixed seed: the portfolio is reproducible
    OUT.mkdir(parents=True, exist_ok=True)

    for path in OUT.glob("*.json"):
        path.unlink()

    for i in range(1, count + 1):
        claim = make_claim(i, rng)
        (OUT / f"{claim['claim_id']}.json").write_text(
            json.dumps(claim, indent=1), encoding="utf-8"
        )

    print(f"wrote {count} synthetic claims to {OUT}")


if __name__ == "__main__":
    main()
