"""Audit — upload a claim packet, see what will be deducted before the TPA does."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from html import escape
from pathlib import Path

import plotly.graph_objects as go
import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import (  # noqa: E402
    API,
    ROUNDING_NOTE,
    api_get,
    api_post,
    api_upload,
    collect_findings,
    page_header,
    rupees,
    severity_chip,
    verdict_banner,
)

ACCEPTED = ["pdf", "png", "jpg", "jpeg", "webp", "tiff", "tif", "bmp"]

st.session_state.setdefault("packet", None)
st.session_state.setdefault("result", None)
st.session_state.setdefault("uploads", [])

page_header(
    "Claim audit",
    "Upload a claim packet and see the deductions before it goes to the TPA.",
)

profiles = api_get("/api/profiles")
samples = api_get("/api/samples")


# --- input ----------------------------------------------------------------

# One panel, no mode switch. Upload is the main path and the samples sit one click
# below it, so nobody has to understand a mode before they can start.
with st.container():
    files = st.file_uploader(
        "Bill, discharge summary, claim form, reports — add whatever you have",
        type=ACCEPTED,
        accept_multiple_files=True,
        help="PDF or photo. Scans and phone pictures are read with on-device OCR.",
    )

    if st.button("Read documents", type="primary", disabled=not files):
        summaries, failures = [], []
        with st.status(f"Reading {len(files)} document(s)…", expanded=True) as status:
            for f in files:
                st.write(f"Reading **{f.name}**")
                try:
                    summaries.append(api_upload("/api/extract", f.name, f.getvalue()))
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{f.name}: {exc}")
            status.update(
                label="Some documents could not be read" if failures else "Documents read",
                state="error" if failures else "complete",
                expanded=False,
            )
        st.session_state.uploads = summaries
        st.session_state.upload_errors = failures
        st.session_state.result = None

    for message in st.session_state.get("upload_errors") or []:
        st.error(message)

    uploads = st.session_state.uploads
    if uploads:
        line_items = [i for u in uploads for i in u["line_items"]]
        attached = [u["document_id"] for u in uploads if u["document_id"]]

        # A policy schedule states these outright. Prefer the document over a default,
        # and say which is which -- a silently-applied wrong room limit moves the
        # settlement by lakhs.
        found = next((u["policy_terms"] for u in uploads if u.get("policy_terms")), None)

        # The discharge summary states the dates, the diagnosis and the procedure.
        # Everything not stated by a document is asked for below rather than defaulted:
        # these fields used to be inherited wholesale from a sample claim, so every
        # uploaded bill was audited as somebody else's knee replacement.
        clinical = next((u.get("clinical_context") for u in uploads if u.get("clinical_context")), None)

        # Derived from the bill's own ROOM rows by the extractor. None when the bill
        # has no room row, in which case the audit is blocked rather than guessed.
        stay = next((u.get("room_stay") for u in uploads if u.get("room_stay")), None)

        def from_doc(field: str, fallback: int) -> tuple[int, bool]:
            raw = (found or {}).get(field)
            try:
                return int(float(raw)), True
            except (TypeError, ValueError):
                return fallback, False

        def date_from_doc(field: str) -> tuple[date | None, bool]:
            raw = (clinical or {}).get(field)
            try:
                return date.fromisoformat(str(raw)), True
            except (TypeError, ValueError):
                return None, False

        si, si_doc = from_doc("sum_insured", 500_000)
        cap, cap_doc = from_doc("room_rent_cap_per_day", 6_000)
        cp, cp_doc = from_doc("copay_percent", 10)

        st.markdown("###### Policy terms")
        if found:
            st.caption(
                "Read from your policy schedule — edit any value if the document is "
                "out of date."
            )
        else:
            st.caption(
                "No policy schedule uploaded, so these are defaults. Upload the schedule "
                "and the real terms are read from it."
            )

        tick = "  ✓ from document"
        p1, p2, p3, p4 = st.columns(4)
        sum_insured = p1.number_input(
            "Sum insured (₹)" + (tick if si_doc else ""), 50_000, 50_000_000, si, 50_000)
        room_cap = p2.number_input(
            "Room limit (₹/day)" + (tick if cap_doc else ""), 0, 200_000, cap, 500,
            help="0 means the policy sets no room rent limit.")
        copay = p3.number_input("Co-pay (%)" + (tick if cp_doc else ""), 0, 50, cp, 5)
        claim_type = p4.selectbox("Claim type", ["cashless", "reimbursement"])

        # --- the stay -----------------------------------------------------
        #
        # The room rate and the number of days drive the two largest deductions the
        # engine produces: the room rent excess and the proportionate scaling of every
        # associated charge. They are read off the bill's own ROOM rows and shown here
        # so they can be corrected -- never defaulted silently.

        st.markdown("###### Stay and clinical details")
        if stay:
            st.caption(
                "Room read from the bill. Dates and diagnosis come from the discharge "
                "summary when you upload one — anything blank has to be filled in, "
                "because the audit is only as honest as what it was given."
            )
        else:
            st.caption(
                "No room charge found on the bill, so the stay has to be entered by hand. "
                "Getting the room rate wrong moves the settlement by lakhs."
            )

        bill_tick = "  ✓ from bill"
        s1, s2, s3, s4 = st.columns(4)
        room_category = s1.text_input(
            "Room category" + (bill_tick if stay else ""),
            value=(stay or {}).get("room_category", ""),
            placeholder="Single AC / ICU",
        )
        room_rate = s2.number_input(
            "Room rate (₹/day)" + (bill_tick if stay else ""),
            0, 500_000, int(float((stay or {}).get("rate_per_day") or 0)), 500,
        )
        room_days = s3.number_input(
            "Room days" + (bill_tick if stay else ""),
            0, 365, int((stay or {}).get("days") or 0),
        )
        is_icu = s4.checkbox(
            "ICU stay",
            value=bool((stay or {}).get("is_icu")),
            help="Switches the check to the policy's ICU limit instead of the room limit.",
        )

        adm_doc_value, adm_doc = date_from_doc("admission_date")
        dis_doc_value, dis_doc = date_from_doc("discharge_date")
        # A stay of N room days ending today is a guess, but it is a self-consistent one
        # and the user is looking straight at both fields. The previous behaviour was to
        # inherit a sample claim's dates, which were consistent with nothing.
        span = max(int(room_days), 1)
        default_discharge = dis_doc_value or date.today()
        default_admission = adm_doc_value or (default_discharge - timedelta(days=span))

        c1, c2, c3, c4 = st.columns(4)
        admission = c1.date_input(
            "Admission" + (tick if adm_doc else ""), value=default_admission, format="DD/MM/YYYY"
        )
        discharge = c2.date_input(
            "Discharge" + (tick if dis_doc else ""), value=default_discharge, format="DD/MM/YYYY"
        )
        diagnosis = c3.text_input(
            "Primary diagnosis" + (tick if (clinical or {}).get("primary_diagnosis") else ""),
            value=(clinical or {}).get("primary_diagnosis") or "",
            placeholder="Required",
        )
        procedure = c4.text_input(
            "Procedure" + (tick if (clinical or {}).get("procedure_performed") else ""),
            value=(clinical or {}).get("procedure_performed") or "",
            placeholder="Leave blank if medical",
        )

        e1, e2 = st.columns(2)
        preauth = e1.number_input(
            "Pre-authorised amount (₹)", 0, 50_000_000, 0, 10_000,
            help="0 means no pre-authorisation was issued — leave it at 0 rather than "
            "guessing, or the variance check fires against a number nobody agreed to.",
        )
        implant = e2.checkbox(
            "Implant used",
            value=bool(
                any(u.get("has_implant") for u in uploads)
                or (clinical or {}).get("involves_implant")
            ),
            help="Read from the bill's implant lines and the discharge summary. Drives "
            "the implant invoice requirement on the readiness check.",
        )

        # --- gate ---------------------------------------------------------
        #
        # Every one of these blocks the audit rather than falling back to a default.
        # A missing field that quietly becomes a plausible number is the exact defect
        # this screen used to have.
        blockers: list[str] = []
        if not line_items:
            blockers.append(
                "None of these documents contained an itemised bill, so there are no "
                "charges to audit. Add the hospital bill."
            )
        if room_rate <= 0 or room_days <= 0:
            blockers.append(
                "Enter the room rate and the number of room days. The room-rent "
                "deduction cannot be estimated without them."
            )
        if not diagnosis.strip():
            blockers.append("Enter the primary diagnosis — it drives the document checklist.")
        if discharge < admission:
            blockers.append("The discharge date is before the admission date.")

        for message in blockers:
            st.warning(message)

        if st.button("Run audit", type="primary", disabled=bool(blockers)):
            with st.status("Auditing…", expanded=True) as status:
                st.write("Classifying charges against the rule catalog")
                for index, item in enumerate(line_items, 1):
                    item["line_no"] = index

                # Built from what was read and what was confirmed on screen. Nothing
                # here is inherited from data/samples -- that inheritance is the bug
                # this replaces.
                packet = {
                    "claim_id": f"UPLOAD-{Path(uploads[0]['filename']).stem}"[:40],
                    "context": {
                        "claim_type": claim_type,
                        "admission_date": admission.isoformat(),
                        "discharge_date": discharge.isoformat(),
                        "primary_diagnosis": diagnosis.strip(),
                        "procedure_performed": procedure.strip() or None,
                        "is_accident": bool((clinical or {}).get("is_accident")),
                        "is_maternity": bool((clinical or {}).get("is_maternity")),
                        "involves_implant": bool(implant),
                        "preauth_approved_amount": str(preauth) if preauth else None,
                        "documents_attached": attached,
                    },
                    "policy": {
                        # Absent terms stay absent. The waterfall already reads None as
                        # "no limit" and 0 as "no deductible", so an unstated sub-limit
                        # must not become an invented one.
                        "policy_id": ((found or {}).get("policy_no") or "not stated"),
                        "sum_insured": str(sum_insured),
                        "balance_sum_insured": str(sum_insured),
                        "room_rent_cap_per_day": str(room_cap) if room_cap else None,
                        "icu_cap_per_day": (
                            str((found or {}).get("icu_cap_per_day"))
                            if (found or {}).get("icu_cap_per_day")
                            else None
                        ),
                        "copay_percent": str(copay),
                        "deductible": "0",
                        "procedure_sublimits": {},
                    },
                    "room_stay": {
                        "room_category": room_category.strip() or "Room",
                        "rate_per_day": str(room_rate),
                        "days": int(room_days),
                        "is_icu": bool(is_icu),
                    },
                    "line_items": line_items,
                }

                st.write("Computing deductions and verifying")
                st.session_state.packet = packet
                # Guarded. This was the one unprotected call on the primary path: a 500
                # raised a raw traceback into the page and left the status widget
                # spinning "Auditing…" forever, because status.update() never ran.
                try:
                    st.session_state.result = api_post("/api/audit", packet)
                    status.update(label="Audit complete", state="complete", expanded=False)
                except Exception as exc:  # noqa: BLE001
                    status.update(label="Audit failed", state="error", expanded=True)
                    st.error(
                        f"The audit could not be completed: {exc}\n\n"
                        f"Your uploaded documents are still loaded — correct the details "
                        f"above and try again."
                    )

SAMPLE_LABEL = {
    "billing_error": "Billing errors",
    "cardiac": "Room-rent trap",
    "clean": "Clean claim",
    "incomplete": "Missing documents",
}

st.markdown(
    '<div class="ciq-or">No documents to hand? Audit a worked example</div>',
    unsafe_allow_html=True,
)
cols = st.columns(len(samples))
for col, sample_name in zip(cols, samples, strict=True):
    label = SAMPLE_LABEL.get(sample_name, sample_name.replace("_", " ").title())
    if col.button(label, use_container_width=True, key=f"s_{sample_name}"):
        with st.status("Auditing…", expanded=False) as status:
            try:
                packet = api_get(f"/api/samples/{sample_name}")
                st.session_state.packet = packet
                st.session_state.result = api_post("/api/audit", packet)
                st.session_state.uploads = []
                status.update(label="Audit complete", state="complete")
            except Exception as exc:  # noqa: BLE001
                status.update(label="Could not load the example", state="error")
                st.error(f"The worked example could not be audited: {exc}")


# --- what was read --------------------------------------------------------

if st.session_state.uploads:
    st.markdown("###### Documents read")
    for got in st.session_state.uploads:
        rows = len(got["line_items"])
        detail = f"{rows} line items" if rows else got["document_label"]
        st.markdown(
            f'<div class="ciq-file"><span>{got["filename"]}</span>'
            f'<span class="ciq-meta"><span class="ciq-tag">{got["file_kind"]}</span>'
            f"&nbsp;&nbsp;{detail}</span></div>",
            unsafe_allow_html=True,
        )
    low = sum(g["low_confidence"] for g in st.session_state.uploads)
    if low:
        st.caption(
            f"{low} row(s) were hard to read and are marked low-confidence — check them "
            "in the line item table below."
        )

result = st.session_state.result
if result is None:
    # Shown only before a result exists. Once there are numbers on screen the numbers
    # are the argument, and this comes down.
    st.markdown(
        """
<div class="ciq-value">
  <div class="ciq-value-head">The deduction you find out about too late</div>
  <p>A hospital submits a claim, waits three weeks, and gets back less than it billed.
  By then the patient has gone home and the money is written off. ClaimIQ reads the
  packet <b>before</b> it goes to the TPA and splits the bill three ways.</p>
  <div class="ciq-value-grid">
    <div class="ciq-value-card ciq-vc-green">
      <div class="ciq-vc-label">The insurer pays</div>
      <div class="ciq-vc-text">What actually settles once room limits, sub-limits and
      co-pay are applied — shown as a range, because insurers read the rules differently.</div>
    </div>
    <div class="ciq-value-card ciq-vc-blue">
      <div class="ciq-vc-label">The patient pays</div>
      <div class="ciq-vc-text">Optional items and policy deductions. Worth saying at
      admission, not at the discharge counter.</div>
    </div>
    <div class="ciq-value-card ciq-vc-red">
      <div class="ciq-vc-label">The hospital absorbs</div>
      <div class="ciq-vc-text">Charges already covered by the room or procedure rate.
      Billed anyway, paid by nobody — and it repeats on every claim until someone
      notices. This is the number no other tool shows you.</div>
    </div>
  </div>
  <p class="ciq-value-foot">Upload the bill and whatever else you have — discharge
  summary, claim form, policy schedule, reports. Scans and phone photos are read on
  this machine. Every deduction cites the rule it came from.</p>
</div>
""",
        unsafe_allow_html=True,
    )
    st.stop()


# --- results --------------------------------------------------------------

profile = st.session_state.get("profile", "typical")
wf = result["profiles"][profile]
gross = float(result["gross_bill"])
settlements = [float(p["projected_settlement"]) for p in result["profiles"].values()]

st.divider()

# The verdict comes before any number. A settlement figure the engine could not stand
# behind should not be the first thing on the screen.
verdict_banner(result)

coverage = float(result.get("coverage", 1))
if coverage < 1:
    st.caption(
        f"{coverage:.0%} of this bill was assessed against a rule. "
        f"Unassessed charges are counted as payable, so the settlement is an upper bound."
    )

for problem in result["verify_problems"]:
    st.error(f"Internal check failed: {problem}")
for degradation in result.get("errors") or []:
    st.warning(degradation)

# The write-off leads. The other three numbers describe this claim; this one describes
# the billing template, so it is the only number on the screen that is still true
# tomorrow -- and it is the number the hospital can actually act on.
m1, m2, m3, m4 = st.columns(4)
with m1:
    st.markdown('<div class="ciq-loss">', unsafe_allow_html=True)
    st.metric(
        "Hospital absorbs", rupees(wf["hospital_writeoff"]),
        help="Items already covered by room, procedure or treatment charges. The "
        "hospital cannot bill these to the insurer or the patient — this loss repeats "
        "on every claim until the billing template is corrected.",
    )
    st.markdown("</div>", unsafe_allow_html=True)
m2.metric("Likely settlement", rupees(wf["projected_settlement"]))
m3.metric("Patient pays", rupees(wf["patient_liability"]))
m4.metric("Gross bill", rupees(gross))

# Naming the repeating items is the argument. The amount is this claim; the item names
# are every claim that carries them.
repeating = sorted(
    {
        f["description"].strip()
        for f in result["findings"]
        if f["bearer"] == "HOSPITAL" and float(f["deducted_amount"]) > 0
    }
)
if repeating:
    shown = ", ".join(repeating[:4])
    more = f" and {len(repeating) - 4} more" if len(repeating) > 4 else ""
    st.markdown(
        f"**{rupees(wf['hospital_writeoff'])} of this bill is a billing-template error, "
        f"not a patient cost.** {len(repeating)} item(s) — {shown}{more} — are already "
        f"covered by charges the hospital raised elsewhere. Every claim carrying these "
        f"lines loses the same money, until the master is corrected."
    )

st.caption(
    f"Range across insurer interpretations: {rupees(min(settlements))} – "
    f"{rupees(max(settlements))}.  ·  "
    f"{'Charges read by AI' if result['ai_used'] else 'Deterministic rules only'}  ·  "
    f"{ROUNDING_NOTE}"
)

# Standing limitations of the tool, not findings about this claim. Collapsed because
# they do not change between audits -- but present, because they are true.
if disclosures := result.get("disclosures"):
    with st.expander(f"How this was checked, and what it does not cover ({len(disclosures)})"):
        for note in disclosures:
            st.markdown(f"- {note}")

# --- waterfall ------------------------------------------------------------

patient_items = sum(
    float(f["deducted_amount"]) for f in result["findings"] if f["bearer"] == "PATIENT"
)
labels, values, measures = ["Gross bill"], [gross], ["absolute"]
if patient_items:
    labels.append("Patient items")
    values.append(-patient_items)
    measures.append("relative")
if float(wf["hospital_writeoff"]):
    labels.append("Hospital absorbs")
    values.append(-float(wf["hospital_writeoff"]))
    measures.append("relative")
for d in wf["policy_deductions"]:
    labels.append(d["step"].replace("_", " ").title())
    values.append(-float(d["amount"]))
    measures.append("relative")
labels.append("Settlement")
values.append(0)
measures.append("total")

fig = go.Figure(
    go.Waterfall(
        orientation="v", measure=measures, x=labels, y=values,
        connector={"line": {"color": "rgba(154,160,170,.35)"}},
        decreasing={"marker": {"color": "#ff5c5c"}},
        increasing={"marker": {"color": "#5aa9ff"}},
        totals={"marker": {"color": "#ff7a1a"}},
    )
)
fig.update_layout(
    height=380, margin=dict(t=10, b=10, l=0, r=0), showlegend=False,
    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#9aa0aa"),
    xaxis=dict(gridcolor="rgba(255,255,255,.05)"),
    yaxis=dict(gridcolor="rgba(255,255,255,.05)"),
)
st.plotly_chart(fig, use_container_width=True)

try:
    sim = api_post("/api/simulate-room", st.session_state.packet, profile=profile)
    if sim.get("applicable"):
        st.success(
            f"A room within the policy limit would have settled "
            f"**{rupees(sim['gain'])} more** — worth telling the patient at admission."
        )
except Exception as exc:  # noqa: BLE001
    # This panel is the room-rent argument in one sentence, so its disappearing without
    # a word is worse than the error itself. It used to be swallowed silently.
    st.caption(f"Room-downgrade comparison unavailable: {exc}")

# The arithmetic behind every bar. `basis`, `inputs` and `formula` were rendered only
# in the PDF; on screen the largest deduction the engine produces was an unlabelled
# negative bar. A number the user cannot reproduce is a number they have to take on
# trust, which is the opposite of what an auditor is for.
if wf["policy_deductions"]:
    with st.expander("How each policy deduction was calculated"):
        for d in wf["policy_deductions"]:
            st.markdown(
                f"**{d['step'].replace('_', ' ').title()} — {rupees(d['amount'])}**  \n"
                f"{d['basis']}"
            )
            if d.get("inputs"):
                for key, value in d["inputs"].items():
                    st.markdown(f"- {key}: `{value}`")
            if d.get("formula"):
                st.markdown(
                    f'<div class="ciq-math">{escape(d["formula"])}</div>',
                    unsafe_allow_html=True,
                )
            st.write("")
        st.caption(ROUNDING_NOTE)


# --- detail ---------------------------------------------------------------

findings = collect_findings(result)

tab_findings, tab_items, tab_actions = st.tabs(
    [f"Findings ({len(findings)})", "All line items", "What to do"]
)

with tab_findings:
    # Sorted by severity, each expandable to the rule, the citation and the arithmetic.
    # This replaces an eight-column dataframe that showed the reason as truncated grid
    # text and dropped `cited_chunk_id` entirely -- so the product's headline claim,
    # "every deduction cites the rule it came from", was nowhere near the deductions.
    if not findings:
        st.success(
            "No findings. Every line matched a rule or a primary billing head, all "
            "required documents are attached, and the consistency checks passed."
        )
    else:
        st.caption(
            "Most urgent first. Open any finding for the rule, its citation and the "
            "arithmetic, then copy it into your billing system."
        )

    for f in findings:
        impact = f.get("impact")
        money = f"  ·  {rupees(impact)}" if impact and float(impact) else ""
        with st.expander(f"[{f['severity']}]  {f['title']}{money}"):
            st.markdown(
                severity_chip(f["severity"]) + f"&nbsp;&nbsp;{escape(f['detail'])}",
                unsafe_allow_html=True,
            )

            if f.get("field"):
                o1, o2 = st.columns(2)
                o1.markdown(f"**Found**  \n{f.get('observed') or '—'}")
                o2.markdown(f"**Expected**  \n{f.get('expected') or '—'}")
                st.caption(f"Field: `{f['field']}`")

            if f.get("citation"):
                st.markdown(
                    f'<div class="ciq-cite">{escape(f["rule_id"])} — {escape(f["citation"])}</div>',
                    unsafe_allow_html=True,
                )
                # The rule's own words, fetched on open rather than up front: 20-odd
                # findings would otherwise be 20 requests before the page paints.
                if f.get("rule_id", "").startswith(("L", "POL")):
                    try:
                        chunk = api_get(f"/api/rules/{f['rule_id']}")
                        st.markdown(f"> **{chunk['title']}** — {chunk['body']}")
                    except Exception as exc:  # noqa: BLE001
                        st.caption(f"Rule text unavailable: {exc}")

            # One block, selectable, with Streamlit's own copy button on hover.
            st.caption("Copy for your billing system")
            st.code(
                f"[{f['severity']}] {f['title']}\n"
                f"{f['detail']}\n"
                + (f"Amount: {rupees(impact)}\n" if impact and float(impact) else "")
                + (f"Rule: {f['rule_id']} — {f['citation']}\n" if f.get("citation") else ""),
                language=None,
            )

with tab_items:
    only_flagged = st.checkbox("Only show deducted and unmatched items")
    st.dataframe(
        [
            {
                "#": f["line_no"],
                "Item": f["description"],
                "Category": f["head"].title(),
                "Amount": rupees(f["amount"]),
                "Verdict": f["classification"].replace("LIST_", "List ").replace("_", " ").title(),
                "Cost falls on": f["bearer"].title(),
                "Deducted": rupees(f["deducted_amount"]),
                "Rule": f.get("cited_chunk_id") or "—",
                "Why": f["reason"],
            }
            for f in result["findings"]
            if not only_flagged
            or float(f["deducted_amount"]) > 0
            or f["classification"] == "UNMAPPED"
        ],
        use_container_width=True, hide_index=True,
    )
    st.caption(ROUNDING_NOTE)

with tab_actions:
    st.markdown(result["narrative"])
    for i, action in enumerate(result["action_list"], 1):
        st.markdown(f"**{i}.** {action}")

    st.divider()
    if st.button("Build audit report (PDF)"):
        with st.spinner("Rendering…"):
            pdf = requests.post(
                f"{API}/api/report", json=st.session_state.packet,
                params={"profile": profile}, timeout=600,
            )
        st.download_button(
            "Download report", data=pdf.content,
            file_name=f"{result['claim_id']}-audit.pdf", mime="application/pdf",
        )
