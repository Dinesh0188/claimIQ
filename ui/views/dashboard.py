"""Portfolio leakage dashboard — where money leaks across the whole book of claims."""

from __future__ import annotations

import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import HOSPITAL, PATIENT, api_get, page_header, rupees  # noqa: E402

page_header(
    "Where the money goes",
    "Across every claim audited, not one bill at a time.",
)


def load(path: str, label: str, **params):
    """Fetch, or show what went wrong and carry on.

    This module had no error handling at all across five API calls, so any one of them
    failing put a raw Python traceback on the page and killed everything below it.
    A dashboard is a set of independent panels; one failing panel should cost one
    panel.
    """
    try:
        return api_get(path, **params)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"{label} could not be loaded: {exc}")
        return None


summary = load("/api/analytics/summary", "Portfolio summary")
if summary is None:
    st.stop()

if summary.get("empty"):
    # Was two shell commands. A billing clerk does not run Python scripts, and the one
    # thing they *can* do from here is audit a claim, so that is what this offers.
    st.info(
        "No claims have been audited yet. Once you check a claim it is recorded here, "
        "and this page starts showing which billing mistakes cost the most across your "
        "whole book."
    )
    st.page_link("views/audit.py", label="Check a claim", icon=None)
    st.caption(
        "Populating a demo portfolio instead: run `python scripts/gen_samples.py 300` "
        "then `python scripts/seed_db.py`."
    )
    st.stop()

st.caption(
    f"{summary['claims']} claims · {summary['ai_claims']} read by AI, "
    f"{summary['claims'] - summary['ai_claims']} by deterministic rules · synthetic data"
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
c4.metric("Avg deduction", f"{float(summary['avg_deduction_pct']):.1f}%")

c5, c6, c7, c8 = st.columns(4)
c5.metric("Est. settled", rupees(summary["settlement"]))
c6.metric("Patient liability", rupees(summary["patient"]))
c7.metric("Room-rent deductions", rupees(summary["room_rent_deduction"]))
c8.metric("Document gaps", f"{summary['doc_gaps']:,}")

st.divider()

left, right = st.columns([3, 2])

with left:
    st.subheader("Where the money goes")
    leakage = load("/api/analytics/leakage", "Leakage breakdown")
    if leakage:
        fig = px.bar(
            leakage, x="amount", y="cause", orientation="h", color="bearer",
            # The app's own colours. This chart used #d1495b / #457b9d -- a different
            # red and a different blue from the ones the rest of the product assigns
            # meaning to, while the caption underneath named them by hue.
            color_discrete_map={"HOSPITAL": HOSPITAL, "PATIENT": PATIENT},
            labels={"amount": "₹ deducted", "cause": "", "bearer": "Borne by"},
        )
        fig.update_layout(
            height=380, margin=dict(t=20, b=10), yaxis=dict(autorange="reversed"),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#9aa0aa"),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Bars marked *hospital* are the hospital's own money — billing errors that "
            "repeat on every claim. Bars marked *patient* are borne by patients under "
            "policy terms."
        )
    elif leakage is not None:
        st.caption("No deductions recorded yet across the portfolio.")

with right:
    st.subheader("Most-missed documents")
    docs = load("/api/analytics/missing-docs", "Missing documents", limit=8)
    if docs:
        st.dataframe(
            [{"Document": d["name"], "Severity": d["severity"], "Claims": d["claims"]} for d in docs],
            use_container_width=True, hide_index=True,
        )
    elif docs is not None:
        st.caption("No document gaps recorded — every audited claim arrived complete.")

st.subheader("Top billing mistakes by aggregate loss")
items = load("/api/analytics/top-items", "Top leaking items", limit=10)
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
elif items is not None:
    st.caption("No hospital-borne deductions recorded yet.")
