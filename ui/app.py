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

# No page_icon: an emoji favicon is the first thing a user sees and the last thing
# a clinical tool should look like. Streamlit falls back to its own mark.
st.set_page_config(page_title=f"{BRAND} — claim audit", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

health = require_api()
st.session_state["health"] = health
sidebar(health)

navigation = st.navigation(
    [
        st.Page(UI_DIR / "views" / "audit.py", title="Check a claim", default=True),
        st.Page(UI_DIR / "views" / "dashboard.py", title="Leakage dashboard"),
        st.Page(UI_DIR / "views" / "ask.py", title="Ask the portfolio"),
        st.Page(UI_DIR / "views" / "trace.py", title="How it decided"),
    ]
)
navigation.run()
