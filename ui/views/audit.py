"""Audit — upload a claim packet, see what will be deducted before the TPA does."""

from __future__ import annotations

import sys
from pathlib import Path

import plotly.graph_objects as go
import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import (  # noqa: E402
    API,
    api_get,
    api_post,
    api_upload,
    page_header,
    rupees,
)

ACCEPTED = ["pdf", "png", "jpg", "jpeg", "webp", "tiff", "tif", "bmp"]
SEVERITY_ICON = {"BLOCKER": "🔴", "QUERY_LIKELY": "🟠", "ADVISORY": "⚪"}

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

        def from_doc(field: str, fallback: int) -> tuple[int, bool]:
            raw = (found or {}).get(field)
            try:
                return int(float(raw)), True
            except (TypeError, ValueError):
                return fallback, False

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

        if not line_items:
            st.warning(
                "None of these documents contained an itemised bill, so there are no "
                "charges to audit. Add the hospital bill."
            )
        elif st.button("Run audit", type="primary"):
            with st.status("Auditing…", expanded=True) as status:
                st.write("Classifying charges against the rule catalog")
                packet = api_get(f"/api/samples/{samples[0]}")
                for index, item in enumerate(line_items, 1):
                    item["line_no"] = index
                packet["line_items"] = line_items
                packet["claim_id"] = f"UPLOAD-{Path(uploads[0]['filename']).stem}"[:40]
                packet["policy"]["sum_insured"] = str(sum_insured)
                packet["policy"]["balance_sum_insured"] = str(sum_insured)
                packet["policy"]["room_rent_cap_per_day"] = str(room_cap) if room_cap else None
                packet["policy"]["copay_percent"] = str(copay)
                packet["context"]["claim_type"] = claim_type
                packet["context"]["documents_attached"] = attached

                st.write("Computing deductions and verifying")
                st.session_state.packet = packet
                st.session_state.result = api_post("/api/audit", packet)
                status.update(label="Audit complete", state="complete", expanded=False)

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
        packet = api_get(f"/api/samples/{sample_name}")
        with st.status("Auditing…", expanded=False) as status:
            st.session_state.packet = packet
            st.session_state.result = api_post("/api/audit", packet)
            st.session_state.uploads = []
            status.update(label="Audit complete", state="complete")


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

m1, m2, m3, m4 = st.columns(4)
m1.metric("Gross bill", rupees(gross))
m2.metric("Likely settlement", rupees(wf["projected_settlement"]))
m3.metric("Patient pays", rupees(wf["patient_liability"]))
with m4:
    st.markdown('<div class="ciq-loss">', unsafe_allow_html=True)
    st.metric(
        "Hospital absorbs", rupees(wf["hospital_writeoff"]),
        help="Items already covered by room, procedure or treatment charges. The "
        "hospital cannot bill these to the insurer or the patient — this loss repeats "
        "on every claim until the billing template is corrected.",
    )
    st.markdown("</div>", unsafe_allow_html=True)

st.caption(
    f"Range across insurer interpretations: {rupees(min(settlements))} – "
    f"{rupees(max(settlements))}.  ·  "
    f"{'Charges read by AI' if result['ai_used'] else 'Deterministic rules only'}  ·  "
    f"{'Checks passed' if result['verify_passed'] else 'Checks FAILED'}"
)

for problem in result["verify_problems"]:
    st.error(f"Verification: {problem}")
for degradation in result.get("errors") or []:
    st.warning(degradation)

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
        connector={"line": {"color": "rgba(148,163,184,.5)"}},
        decreasing={"marker": {"color": "#d1495b"}},
        totals={"marker": {"color": "#0e7c6b"}},
    )
)
fig.update_layout(
    height=360, margin=dict(t=10, b=10, l=0, r=0), showlegend=False,
    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
)
st.plotly_chart(fig, use_container_width=True)

try:
    sim = api_post("/api/simulate-room", st.session_state.packet, profile=profile)
    if sim.get("applicable"):
        st.success(
            f"A room within the policy limit would have settled "
            f"**{rupees(sim['gain'])} more** — worth telling the patient at admission."
        )
except Exception:  # noqa: BLE001
    pass


# --- detail ---------------------------------------------------------------

tab_items, tab_docs, tab_actions = st.tabs(["Line items", "Readiness", "What to do"])

with tab_items:
    only_flagged = st.checkbox("Only show deducted and unmatched items")
    st.dataframe(
        [
            {
                "#": f["line_no"],
                "Item": f["description"],
                "Category": f["head"].title(),
                "Amount": float(f["amount"]),
                "Verdict": f["classification"].replace("LIST_", "List ").replace("_", " ").title(),
                "Cost falls on": f["bearer"].title(),
                "Deducted": float(f["deducted_amount"]),
                "Why": f["reason"],
            }
            for f in result["findings"]
            if not only_flagged
            or float(f["deducted_amount"]) > 0
            or f["classification"] == "UNMAPPED"
        ],
        use_container_width=True, hide_index=True,
    )

with tab_docs:
    d1, d2 = st.columns(2)
    with d1:
        st.markdown("**Missing documents**")
        if not result["document_gaps"]:
            st.success("Nothing missing.")
        for gap in result["document_gaps"]:
            st.markdown(f"{SEVERITY_ICON[gap['severity']]} **{gap['name']}** — {gap['reason']}")
    with d2:
        st.markdown("**Inconsistencies**")
        if not result["consistency_flags"]:
            st.success("None found.")
        for flag in result["consistency_flags"]:
            st.markdown(f"{SEVERITY_ICON[flag['severity']]} {flag['message']}")

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
