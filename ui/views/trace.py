"""Agent trace: what each node did, what it cost, and whether the repair loop fired."""

from __future__ import annotations

import sys
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import api_get, page_header  # noqa: E402

page_header("How this claim was processed", "Each step, what it cost, and what it checked.")

result = st.session_state.get("result")
if result is None:
    # Was a dead end: it named the action and offered no way to take it.
    st.info("Check a claim first — this page then shows how that claim was processed.")
    st.page_link("views/audit.py", label="Check a claim", icon=None)
    st.stop()

try:
    run = api_get(f"/api/trace/{result['claim_id']}")
except Exception as exc:  # noqa: BLE001
    # A bare `except` here asserted one specific cause -- "no trace stored" -- for a
    # network failure, a 500 and a genuine 404 alike. If the API just went down, the
    # user was told their trace was never written.
    st.warning(f"The processing trace could not be loaded: {exc}")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total latency", f"{run['total_ms']:,} ms")
c2.metric("Tokens", f"{run['total_tokens']:,}")
c3.metric("LLM calls", sum(n["llm_calls"] for n in run["nodes"]))
c4.metric("Cache hits", sum(n["cache_hits"] for n in run["nodes"]))

executions = [n["node"] for n in run["nodes"]]
st.markdown("**Execution path:** " + " → ".join(f"`{n}`" for n in executions))

if executions.count("explain") > 1:
    st.success(
        f"The repair loop fired — `explain` ran {executions.count('explain')} times. "
        "The verifier rejected the first narrative and re-prompted with the specific "
        "problem rather than blindly retrying."
    )
else:
    st.info("Verifier passed on the first attempt; no repair was needed.")

fig = go.Figure(
    go.Bar(
        x=[n["latency_ms"] for n in run["nodes"]],
        y=[f"{i+1}. {n['node']}" for i, n in enumerate(run["nodes"])],
        orientation="h",
        marker_color=["#d1495b" if n["llm_calls"] else "#2a9d8f" for n in run["nodes"]],
        text=[f"{n['latency_ms']} ms" for n in run["nodes"]],
    )
)
fig.update_layout(
    height=60 + 34 * len(run["nodes"]),
    margin=dict(t=24, b=10),
    xaxis_title="latency (ms) — red nodes made LLM calls",
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig, use_container_width=True)

st.dataframe(
    [
        {
            "#": i + 1,
            "Node": n["node"],
            "Latency (ms)": n["latency_ms"],
            "LLM calls": n["llm_calls"],
            "Cached": n["cache_hits"],
            "Prompt tok": n["prompt_tokens"],
            "Completion tok": n["completion_tokens"],
            "Attempts": n["attempts"],
            "Error": n["error"],
        }
        for i, n in enumerate(run["nodes"])
    ],
    use_container_width=True,
    hide_index=True,
)

st.subheader("Grounding")
cited = [f for f in result["findings"] if f["cited_chunk_id"]]
st.caption(
    f"{len(cited)} of {sum(1 for f in result['findings'] if f['classification'] != 'PAYABLE')} "
    "non-payable determinations carry a citation. Uncited ones are rejected by the "
    "verifier and downgraded to UNMAPPED rather than being trusted."
)
for finding in cited[:25]:
    with st.expander(f"`{finding['cited_chunk_id']}` — {finding['description']}"):
        try:
            chunk = api_get(f"/api/rules/{finding['cited_chunk_id']}")
            st.markdown(f"**{chunk['title']}** · {chunk['meta'].get('list', 'policy')}")
            if chunk["aliases"]:
                st.caption("Aliases: " + ", ".join(chunk["aliases"]))
            st.write(chunk["body"])
        except Exception:  # noqa: BLE001
            st.warning("Chunk not found in the corpus.")
