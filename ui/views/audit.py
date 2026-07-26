"""ClaimIQ — audit screen."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import plotly.graph_objects as go
import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import API, TAGLINE, api_get, api_post, hero, note, rupees  # noqa: E402

health = st.session_state["health"]
hero("ClaimIQ", TAGLINE, tags=True)

profiles = api_get("/api/profiles")
profile = st.sidebar.selectbox(
    "Insurer profile",
    list(profiles),
    index=list(profiles).index("typical"),
    format_func=lambda p: profiles[p]["label"],
)
st.sidebar.caption(profiles[profile]["note"])

note(
    "Audit a hospital claim packet <b>before</b> it goes to the TPA. Three numbers come "
    "out: what the insurer will likely settle, what the patient owes, and — the one "
    "nobody else shows you — what the <b>hospital</b> is quietly writing off on every "
    "claim it files."
)

st.session_state.setdefault("packet", None)
st.session_state.setdefault("result", None)

tab_sample, tab_pdf = st.tabs(["Load a sample", "Upload a bill PDF"])

with tab_sample:
    samples = api_get("/api/samples")
    col_a, col_b = st.columns([3, 1])
    name = col_a.selectbox("Sample claim", samples, index=0)
    col_b.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    if col_b.button("Run audit", type="primary", use_container_width=True):
        packet = api_get(f"/api/samples/{name}")
        with st.spinner("Running the agent…"):
            st.session_state.packet = packet
            st.session_state.result = api_post("/api/audit", packet)

with tab_pdf:
    st.caption(
        "A native PDF's ruled table is parsed directly — exact, instant, no model. "
        "Scans with no text layer are rasterised and read by the vision model instead. "
        "Generate test bills of both kinds with `python scripts/gen_bill_pdf.py --scan`."
    )
    upload = st.file_uploader("Hospital bill (PDF)", type=["pdf"])
    base = st.selectbox(
        "Policy terms to apply", samples, index=0, key="pdf_policy",
        help="The bill supplies line items; policy terms come from this sample.",
    )
    if upload and st.button("Extract and audit", type="primary"):
        with st.spinner("Vision model reading the bill…"):
            try:
                extracted = requests.post(
                    f"{API}/api/extract-pdf",
                    files={"file": (upload.name, upload.getvalue(), "application/pdf")},
                    timeout=300,
                ).json()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Extraction failed: {exc}")
                st.stop()

        if "line_items" not in extracted:
            st.error(extracted.get("detail", "Extraction failed."))
            st.stop()

        packet = api_get(f"/api/samples/{base}")
        packet["line_items"] = extracted["line_items"]
        packet["claim_id"] = f"UPLOAD-{upload.name.rsplit('.', 1)[0]}"[:40]
        if extracted.get("room_rate_per_day"):
            packet["room_stay"]["rate_per_day"] = extracted["room_rate_per_day"]
        if extracted.get("room_days"):
            packet["room_stay"]["days"] = extracted["room_days"]

        how = (
            "parsed directly from the PDF's ruled table — no model needed"
            if extracted.get("method") == "text_layer"
            else "read by the vision model (this PDF has no text layer)"
        )
        st.success(
            f"Extracted {len(extracted['line_items'])} line items, {how}. "
            f"{extracted['low_confidence']} row(s) below 0.7 confidence."
        )
        with st.spinner("Running the agent…"):
            st.session_state.packet = packet
            st.session_state.result = api_post("/api/audit", packet)

result = st.session_state.result
if result is None:
    st.info("Load a sample or upload a bill to begin.")
    st.stop()

wf = result["profiles"][profile]
gross = float(result["gross_bill"])
settlements = [float(p["projected_settlement"]) for p in result["profiles"].values()]

# --- headline -------------------------------------------------------------

c1, c2, c3, c4 = st.columns(4)
c1.metric("Gross bill", rupees(gross))
c2.metric("Est. settlement", rupees(wf["projected_settlement"]))
c3.metric("Patient liability", rupees(wf["patient_liability"]))
with c4:
    st.markdown('<div class="ciq-loss">', unsafe_allow_html=True)
    st.metric(
        "Hospital write-off",
        rupees(wf["hospital_writeoff"]),
        help="Lists II/III/IV — items the hospital billed that should have been folded "
        "into room, procedure or treatment cost. This leaks on every claim they file.",
    )
    st.markdown("</div>", unsafe_allow_html=True)

st.caption(
    f"Estimated settlement range across insurer profiles: "
    f"**{rupees(min(settlements))} – {rupees(max(settlements))}**. "
    f"Showing *{profiles[profile]['label']}*. Corpus `{result['corpus_version']}`."
)

status = st.columns(3)
status[0].caption(f"AI pipeline: {'yes' if result['ai_used'] else 'no (deterministic)'}")
status[1].caption(
    f"Verifier: {'passed' if result['verify_passed'] else 'FAILED'} · repairs {result['repair_count']}"
)
status[2].caption(f"Unmapped items: {result['unmapped_count']}")

for problem in result["verify_problems"]:
    st.error(f"Verifier: {problem}")

if result["unmapped_count"]:
    st.warning(
        f"{result['unmapped_count']} line item(s) could not be matched to the catalog. "
        "They were **not** deducted — review them manually."
    )

# --- waterfall ------------------------------------------------------------

patient_items = sum(
    float(f["deducted_amount"]) for f in result["findings"] if f["bearer"] == "PATIENT"
)
labels, values, measures = ["Gross bill"], [gross], ["absolute"]

if patient_items:
    labels.append("List I (patient)")
    values.append(-patient_items)
    measures.append("relative")
if float(wf["hospital_writeoff"]):
    labels.append("Lists II–IV (hospital)")
    values.append(-float(wf["hospital_writeoff"]))
    measures.append("relative")
for d in wf["policy_deductions"]:
    labels.append(d["step"].replace("_", " ").title())
    values.append(-float(d["amount"]))
    measures.append("relative")
labels.append("Est. settlement")
values.append(0)
measures.append("total")

fig = go.Figure(
    go.Waterfall(
        orientation="v", measure=measures, x=labels, y=values,
        connector={"line": {"color": "rgba(128,128,128,0.4)"}},
        decreasing={"marker": {"color": "#d1495b"}},
        totals={"marker": {"color": "#2a9d8f"}},
    )
)
fig.update_layout(height=420, margin=dict(t=30, b=10), showlegend=False)
st.plotly_chart(fig, use_container_width=True)

# --- room downgrade -------------------------------------------------------

try:
    sim = api_post("/api/simulate-room", st.session_state.packet, profile=profile)
    if sim.get("applicable"):
        st.success(
            f"**Room downgrade simulator** — had the patient taken a room inside the "
            f"policy cap, the estimated settlement would rise by "
            f"**{rupees(sim['gain'])}** (to {rupees(sim['result']['projected_settlement'])}). "
            "One conversation at admission is worth that much to this family."
        )
except Exception:  # noqa: BLE001
    pass

# --- findings -------------------------------------------------------------

st.subheader("Line item audit")
only_deducted = st.checkbox("Show only deducted and unmatched items")
rows = [
    {
        "#": f["line_no"],
        "Description": f["description"],
        "Head": f["head"],
        "Amount": float(f["amount"]),
        "Classification": f["classification"],
        "Borne by": f["bearer"],
        "Deducted": float(f["deducted_amount"]),
        "Cited": f["cited_chunk_id"] or "",
        "Conf": round(f["confidence"], 2),
        "Reason": f["reason"],
    }
    for f in result["findings"]
    if not only_deducted
    or float(f["deducted_amount"]) > 0
    or f["classification"] == "UNMAPPED"
]
st.dataframe(rows, use_container_width=True, hide_index=True)

# --- readiness ------------------------------------------------------------

left, right = st.columns(2)
with left:
    st.subheader("Missing documents")
    if not result["document_gaps"]:
        st.success("No gaps detected.")
    for gap in result["document_gaps"]:
        icon = {"BLOCKER": "🔴", "QUERY_LIKELY": "🟠", "ADVISORY": "⚪"}[gap["severity"]]
        st.markdown(f"{icon} **{gap['name']}** — {gap['reason']}")

with right:
    st.subheader("Consistency flags")
    if not result["consistency_flags"]:
        st.success("No inconsistencies detected.")
    for flag in result["consistency_flags"]:
        icon = {"BLOCKER": "🔴", "QUERY_LIKELY": "🟠", "ADVISORY": "⚪"}[flag["severity"]]
        st.markdown(f"{icon} `{flag['check_id']}` {flag['message']}")

# --- narrative ------------------------------------------------------------

st.subheader("What to do about it")
st.caption(
    "Narrative written by the model from the computed figures; the verifier rejects any "
    "number it did not receive." if result["ai_used"] else "Deterministic narrative — AI is off."
)
st.markdown(result["narrative"])
for i, action in enumerate(result["action_list"], 1):
    st.markdown(f"**{i}.** {action}")

# --- export ---------------------------------------------------------------

st.divider()
if st.button("Generate audit report (PDF)"):
    with st.spinner("Rendering…"):
        pdf = requests.post(
            f"{API}/api/report", json=st.session_state.packet,
            params={"profile": profile}, timeout=300,
        )
    st.download_button(
        "Download audit report",
        data=pdf.content,
        file_name=f"{result['claim_id']}-audit.pdf",
        mime="application/pdf",
    )

with st.expander("Raw result (JSON)"):
    st.code(json.dumps(result, indent=2), language="json")
