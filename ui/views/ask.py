"""Natural-language querying over the claims database (guardrailed text-to-SQL)."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import api_post, page_header  # noqa: E402

health = st.session_state["health"]
page_header("Ask about your claims", "Plain English. The query it runs is shown to you.")

if not (health["key_present"] and health["ai_enabled"]):
    st.warning("This page needs a model provider. Add a key in `providers.json`.")
    st.stop()

EXAMPLES = [
    "Which billing mistake cost the hospital the most money?",
    "What are the five largest claims by gross bill?",
    "How much was written off per month?",
    "Which documents are most often missing on accident claims?",
    "What share of claims had a room rent deduction?",
]

st.markdown("**Try:** " + " · ".join(f"*{e}*" for e in EXAMPLES[:3]))
question = st.text_input("Question", placeholder=EXAMPLES[0])

if st.button("Ask", type="primary") and question.strip():
    with st.spinner("Generating and validating SQL…"):
        try:
            answer = api_post("/api/analytics/ask", {"question": question})
        except Exception as exc:  # noqa: BLE001
            st.error(f"Rejected: {exc}")
            st.stop()

    st.caption(answer["explanation"])
    st.code(answer["sql"], language="sql")

    if answer["rows"]:
        st.dataframe(answer["rows"], use_container_width=True, hide_index=True)
        numeric = [
            c for c in answer["columns"]
            if all(isinstance(r.get(c), (int, float)) for r in answer["rows"])
        ]
        if numeric and len(answer["columns"]) >= 2 and len(answer["rows"]) > 1:
            label = next((c for c in answer["columns"] if c not in numeric), None)
            if label:
                st.bar_chart(answer["rows"], x=label, y=numeric[0])
    else:
        st.info("Query ran successfully but returned no rows.")
