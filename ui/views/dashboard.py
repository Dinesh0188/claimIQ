"""Portfolio leakage dashboard — where money leaks across the whole book of claims."""

from __future__ import annotations

import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import api_get, hero, rupees  # noqa: E402

hero(
    "Leakage dashboard",
    "Where the money goes across the whole book of claims — not one bill at a time",
)

summary = api_get("/api/analytics/summary")
if summary.get("empty"):
    st.info(
        "No claims stored yet. Seed the portfolio:\n\n"
        "```\npython scripts/gen_samples.py 300\npython scripts/seed_db.py\n```"
    )
    st.stop()

st.caption(
    f"**Data provenance:** {summary['claims']} claims audited · "
    f"{summary['ai_claims']} through the full LLM agent · "
    f"{summary['claims'] - summary['ai_claims']} through the deterministic engine only. "
    "Running every claim through the agent would exceed free-tier rate limits, so the "
    "split is stated rather than glossed. All data is synthetic."
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Claims audited", f"{summary['claims']:,}")
c2.metric("Gross billed", rupees(summary["gross"]))
c3.metric(
    "Preventable hospital loss",
    rupees(summary["preventable"]),
    help="Lists II/III/IV across the portfolio — billing errors the hospital absorbs. "
    "Recoverable by re-billing, not by chasing the insurer.",
)
c4.metric("Avg deduction", f"{summary['avg_deduction_pct']:.1f}%")

c5, c6, c7, c8 = st.columns(4)
c5.metric("Est. settled", rupees(summary["settlement"]))
c6.metric("Patient liability", rupees(summary["patient"]))
c7.metric("Room-rent deductions", rupees(summary["room_rent_deduction"]))
c8.metric("Document gaps", f"{summary['doc_gaps']:,}")

st.divider()

left, right = st.columns([3, 2])

with left:
    st.subheader("Where the money goes")
    leakage = api_get("/api/analytics/leakage")
    if leakage:
        fig = px.bar(
            leakage, x="amount", y="cause", orientation="h", color="bearer",
            color_discrete_map={"HOSPITAL": "#d1495b", "PATIENT": "#457b9d"},
            labels={"amount": "₹ deducted", "cause": "", "bearer": "Borne by"},
        )
        fig.update_layout(height=380, margin=dict(t=20, b=10), yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Red is the hospital's own money. Those are billing errors that repeat on "
            "every claim; blue is borne by patients under policy terms."
        )

with right:
    st.subheader("Most-missed documents")
    docs = api_get("/api/analytics/missing-docs", limit=8)
    if docs:
        st.dataframe(
            [{"Document": d["name"], "Severity": d["severity"], "Claims": d["claims"]} for d in docs],
            use_container_width=True, hide_index=True,
        )

st.subheader("Top 10 billing mistakes by aggregate loss")
items = api_get("/api/analytics/top-items", limit=10)
if items:
    st.dataframe(
        [
            {
                "Item as billed": row["item"],
                "Claims affected": row["claims"],
                "Total written off": rupees(row["total_deducted"]),
            }
            for row in items
        ],
        use_container_width=True, hide_index=True,
    )
    st.caption(
        "Each row is a line the hospital keeps billing and keeps not being paid for. "
        "Fixing the top three at the billing-master level stops the leak at source."
    )
