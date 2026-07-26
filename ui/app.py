"""ClaimIQ — entry point and router.

Streamlit 1.60 no longer auto-discovers a `pages/` directory, so navigation is
declared explicitly with `st.navigation`. Shared chrome (page config, API health
check, sidebar) lives here so each view only renders its own content.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

UI_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(UI_DIR))

from _shared import BRAND, CSS, require_api, sidebar  # noqa: E402

st.set_page_config(page_title=f"{BRAND} — claim audit", page_icon="🏥", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

health = require_api()
st.session_state["health"] = health
sidebar(health)

navigation = st.navigation(
    [
        st.Page(UI_DIR / "views" / "audit.py", title="Audit", icon="🏥", default=True),
        st.Page(UI_DIR / "views" / "trace.py", title="Agent trace", icon="🔍"),
        st.Page(UI_DIR / "views" / "dashboard.py", title="Leakage dashboard", icon="📊"),
        st.Page(UI_DIR / "views" / "ask.py", title="Ask the portfolio", icon="💬"),
    ]
)
navigation.run()
