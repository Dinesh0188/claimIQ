"""Theme, chrome and API helpers.

Dark surface, single orange accent. Colour carries meaning and is not decoration:
orange is the product, red is money the hospital loses, blue is money the patient
bears, green is money that settles. Nothing else is coloured.
"""

from __future__ import annotations

import requests
import streamlit as st

API = "http://127.0.0.1:8000"
BRAND = "ClaimIQ"

INK = "#0c0d10"        # near-black, the surface everything sits on
PANEL = "#15171c"      # raised card
LINE = "#24272f"       # hairline borders
TEXT = "#e8eaed"       # primary text
MUTED = "#9aa0aa"      # secondary text
ACCENT = "#ff7a1a"     # signature orange
ACCENT_DEEP = "#e05a00"
HOSPITAL = "#ff5c5c"   # money the hospital loses
PATIENT = "#5aa9ff"    # money the patient bears
SETTLED = "#3ddc84"    # money that settles

# Scoped selectors only. An earlier version used
#   section[data-testid="stSidebar"] * { color: ... !important }
# which forced one colour onto every descendant including components carrying their
# own background -- that is what made the sidebar unreadable.
CSS = f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500&display=swap');

  html, body, [class*="css"] {{ font-family: 'Plus Jakarta Sans', system-ui, sans-serif; }}

  .stApp {{
    background:
      radial-gradient(900px 420px at 12% -10%, rgba(255,122,26,.13) 0%, transparent 60%),
      radial-gradient(700px 380px at 92% 4%, rgba(255,122,26,.07) 0%, transparent 55%),
      {INK};
    color: {TEXT};
  }}
  .block-container {{ padding-top: 2.2rem; max-width: 1240px; }}

  h1, h2, h3, h4, p, span, label, li {{ color: {TEXT}; }}
  [data-testid="stCaptionContainer"] p {{ color: {MUTED}; }}

  /* ---------- sidebar ---------- */
  section[data-testid="stSidebar"] {{
    background: #0a0b0e; border-right: 1px solid {LINE};
  }}
  section[data-testid="stSidebar"] .stMarkdown p,
  section[data-testid="stSidebar"] li,
  section[data-testid="stSidebar"] label {{ color: #c7ccd4; }}
  section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{ color: #7e858f; }}
  section[data-testid="stSidebar"] [data-testid="stExpander"] details {{
    background: {PANEL}; border: 1px solid {LINE}; border-radius: 12px;
  }}
  section[data-testid="stSidebar"] [data-testid="stExpander"] summary {{ color: {TEXT}; }}

  /* nav links */
  section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"] {{
    border-radius: 10px; margin-bottom: 2px;
  }}
  section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"]:hover {{
    background: rgba(255,122,26,.10);
  }}
  section[data-testid="stSidebar"] a[aria-current="page"] {{
    background: rgba(255,122,26,.16) !important;
    box-shadow: inset 2px 0 0 {ACCENT};
  }}

  .ciq-brand {{
    font-size: 30px; font-weight: 800; letter-spacing: -1.2px; line-height: 1;
    background: linear-gradient(96deg, #ffb056 0%, {ACCENT} 45%, {ACCENT_DEEP} 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }}
  .ciq-brand-sub {{
    font-size: 10px; font-weight: 700; letter-spacing: 2.6px; text-transform: uppercase;
    color: #6d7480; margin-top: 4px;
  }}
  .ciq-pill {{
    display: inline-flex; align-items: center; gap: 8px; margin-top: 15px;
    padding: 7px 14px; border-radius: 999px; font-size: 11.5px; font-weight: 700;
    background: rgba(255,122,26,.11); border: 1px solid rgba(255,122,26,.30);
    color: #ffab6b;
  }}
  .ciq-dot {{ width: 7px; height: 7px; border-radius: 50%; display: inline-block; }}

  /* ---------- page header ---------- */
  h1.ciq-title {{
    font-size: 38px; font-weight: 800; letter-spacing: -1.4px; margin: 0 0 8px;
    background: linear-gradient(94deg, #ffffff 22%, #ffc590 68%, {ACCENT} 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }}
  p.ciq-sub {{ color: {MUTED}; font-size: 15px; margin: 0 0 28px; font-weight: 500; }}

  /* ---------- metrics ---------- */
  div[data-testid="stMetric"] {{
    background: linear-gradient(168deg, {PANEL} 0%, #101216 100%);
    border: 1px solid {LINE}; border-radius: 16px; padding: 18px 20px;
    transition: transform .16s ease, border-color .16s ease;
  }}
  div[data-testid="stMetric"]:hover {{ transform: translateY(-2px); border-color: #34394a; }}
  div[data-testid="stMetricValue"] {{
    font-size: 30px; font-weight: 800; letter-spacing: -1px; color: {TEXT};
  }}
  div[data-testid="stMetricLabel"] p {{
    font-size: 10.5px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1px; color: {MUTED};
  }}
  .ciq-loss div[data-testid="stMetric"] {{
    background: linear-gradient(168deg, rgba(255,92,92,.13) 0%, {PANEL} 70%);
    border-color: rgba(255,92,92,.34);
  }}
  .ciq-loss div[data-testid="stMetricValue"] {{ color: {HOSPITAL}; }}

  /* ---------- uploaded files ---------- */
  .ciq-file {{
    display: flex; justify-content: space-between; align-items: center; gap: 12px;
    padding: 13px 16px; border: 1px solid {LINE}; border-radius: 13px;
    background: {PANEL}; margin-bottom: 8px; font-size: 13.5px; font-weight: 600;
    color: {TEXT};
  }}
  .ciq-file:hover {{ border-color: rgba(255,122,26,.42); }}
  .ciq-tag {{
    font-size: 10px; font-weight: 800; letter-spacing: .6px; text-transform: uppercase;
    padding: 4px 10px; border-radius: 999px;
    background: rgba(255,122,26,.14); color: #ffb277;
    border: 1px solid rgba(255,122,26,.30);
  }}
  .ciq-meta {{ color: {MUTED}; font-weight: 500; font-size: 12.5px; }}

  /* ---------- buttons ---------- */
  .stButton button {{
    border-radius: 12px; font-weight: 700; padding: .58rem 1.2rem;
    background: {PANEL}; color: {TEXT}; border: 1px solid {LINE};
    transition: all .16s ease;
  }}
  .stButton button:hover:enabled {{
    border-color: rgba(255,122,26,.55); color: #ffb277;
  }}
  .stButton button[kind="primary"] {{
    background: linear-gradient(96deg, {ACCENT} 0%, {ACCENT_DEEP} 100%);
    border: none; color: #160c04;
    box-shadow: 0 10px 26px -12px rgba(255,122,26,.9);
  }}
  .stButton button[kind="primary"]:hover:enabled {{
    transform: translateY(-1px);
    box-shadow: 0 14px 32px -12px rgba(255,122,26,1);
  }}
  .stDownloadButton button {{ border-radius: 12px; font-weight: 700; }}

  /* ---------- upload dropzone ---------- */
  [data-testid="stFileUploaderDropzone"] {{
    background: linear-gradient(160deg, rgba(255,122,26,.06) 0%, {PANEL} 70%);
    border: 2px dashed rgba(255,122,26,.42); border-radius: 18px; padding: 30px;
    transition: all .18s ease;
  }}
  [data-testid="stFileUploaderDropzone"]:hover {{
    border-color: {ACCENT}; background: linear-gradient(160deg, rgba(255,122,26,.11) 0%, {PANEL} 70%);
  }}
  [data-testid="stFileUploaderDropzone"] button {{
    background: rgba(255,122,26,.14); border: 1px solid rgba(255,122,26,.34);
    color: #ffb277;
  }}

  /* ---------- inputs ---------- */
  .stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] > div {{
    background: {PANEL} !important; border-color: {LINE} !important; color: {TEXT} !important;
  }}

  /* ---------- tabs ---------- */
  .stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid {LINE}; }}
  .stTabs [data-baseweb="tab"] {{
    font-weight: 700; font-size: 14px; color: {MUTED};
    border-radius: 11px 11px 0 0; padding: 9px 17px;
  }}
  .stTabs [aria-selected="true"] {{
    color: {ACCENT} !important; background: rgba(255,122,26,.10);
  }}

  div[data-testid="stDataFrame"] {{ border: 1px solid {LINE}; border-radius: 13px; }}
  hr {{ border-color: {LINE}; }}
  code {{ font-family: 'JetBrains Mono', monospace; color: #ffb277; }}

  /* ---------- sample divider ---------- */
  .ciq-or {{
    display: flex; align-items: center; gap: 16px; margin: 30px 0 14px;
    font-size: 12px; font-weight: 700; letter-spacing: .5px; color: {MUTED};
    text-transform: uppercase;
  }}
  .ciq-or::before, .ciq-or::after {{ content: ""; flex: 1; height: 1px; background: {LINE}; }}

  /* ---------- value proposition, empty state only ---------- */
  .ciq-value {{ margin-top: 34px; }}
  .ciq-value-head {{
    font-size: 23px; font-weight: 800; color: {TEXT};
    letter-spacing: -.7px; margin-bottom: 10px;
  }}
  .ciq-value p {{ color: {MUTED}; font-size: 14.5px; line-height: 1.7; max-width: 780px; }}
  .ciq-value-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
    gap: 14px; margin: 24px 0 18px;
  }}
  .ciq-value-card {{
    background: {PANEL}; border: 1px solid {LINE}; border-radius: 16px;
    padding: 20px; border-top: 3px solid {LINE};
  }}
  .ciq-vc-green {{ border-top-color: {SETTLED}; }}
  .ciq-vc-blue  {{ border-top-color: {PATIENT}; }}
  .ciq-vc-red   {{
    border-top-color: {HOSPITAL};
    background: linear-gradient(168deg, rgba(255,92,92,.09) 0%, {PANEL} 70%);
  }}
  .ciq-vc-label {{
    font-size: 10.5px; font-weight: 800; letter-spacing: 1px;
    text-transform: uppercase; color: {MUTED}; margin-bottom: 8px;
  }}
  .ciq-vc-text {{ font-size: 13.5px; line-height: 1.65; color: #b6bcc6; }}
  .ciq-value-foot {{ font-size: 13px !important; color: #7e858f !important; }}
</style>
"""


def _unwrap(response):
    """Data, or an error carrying what the server actually said.

    Calling .json() unconditionally is how "Expecting value: line 1 column 1" reaches
    the screen: the body was not JSON -- a plain-text 500, or empty from a dropped
    connection -- and the parse failure replaced the real cause.
    """
    try:
        payload = response.json()
    except ValueError:
        body = (response.text or "").strip()
        raise RuntimeError(
            f"HTTP {response.status_code}: {body[:400] if body else 'empty response body'}"
        ) from None

    if response.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        raise RuntimeError(detail or f"HTTP {response.status_code}")
    return payload


def api_get(path: str, **params):
    return _unwrap(requests.get(f"{API}{path}", params=params, timeout=180))


def api_post(path: str, payload=None, **params):
    return _unwrap(requests.post(f"{API}{path}", json=payload, params=params, timeout=600))


def api_upload(path: str, filename: str, data: bytes):
    """Long timeout: OCR downloads its model on first use, then runs ~30s a page."""
    return _unwrap(
        requests.post(
            f"{API}{path}",
            files={"file": (filename, data, "application/octet-stream")},
            timeout=900,
        )
    )


def rupees(value) -> str:
    """Indian grouping — 1,23,456 not 123,456. A hospital desk reads in lakhs."""
    number = int(round(float(value)))
    sign = "-" if number < 0 else ""
    digits = str(abs(number))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        digits = ",".join(parts + [tail])
    return f"{sign}₹{digits}"


def page_header(title: str, subtitle: str) -> None:
    st.markdown(
        f'<h1 class="ciq-title">{title}</h1><p class="ciq-sub">{subtitle}</p>',
        unsafe_allow_html=True,
    )


def require_api() -> dict:
    try:
        return api_get("/health")
    except Exception:
        pass

    try:
        from _boot import ensure_api

        if ensure_api():
            return api_get("/health")
    except Exception:  # noqa: BLE001
        pass

    st.markdown(CSS, unsafe_allow_html=True)
    st.error(f"Cannot reach the {BRAND} service at {API}. Start it with `.\\start.ps1`.")
    st.stop()


def sidebar(health: dict) -> None:
    live = health["key_present"] and health["ai_enabled"]
    colour = SETTLED if live else ACCENT
    label = "AI reading enabled" if live else "Deterministic mode"

    st.sidebar.markdown(
        '<div class="ciq-brand">ClaimIQ</div>'
        '<div class="ciq-brand-sub">Pre-submission audit</div>'
        f'<div class="ciq-pill"><span class="ciq-dot" style="background:{colour}"></span>'
        f"{label}</div>",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    try:
        available = api_get("/api/providers")
    except Exception:  # noqa: BLE001
        available = []

    if len(available) > 1:
        names = [p["name"] for p in available]
        current = health.get("provider", names[0])
        chosen = st.sidebar.selectbox(
            "Model provider", names,
            index=names.index(current) if current in names else 0,
        )
        if chosen != current:
            api_post(f"/api/providers/{chosen}")
            st.rerun()

    # Insurers disagree on which charges the room-rent rule scales. Rather than pick
    # one and imply certainty, the estimate is a range and this selects which
    # interpretation the headline figure uses.
    try:
        profiles = api_get("/api/profiles")
        names = list(profiles)
        current = st.session_state.get("profile", "typical")
        picked = st.sidebar.selectbox(
            "Insurer interpretation", names,
            index=names.index(current) if current in names else names.index("typical"),
            format_func=lambda p: profiles[p]["label"],
        )
        st.session_state["profile"] = picked
        st.sidebar.caption(profiles[picked]["note"])
    except Exception:  # noqa: BLE001
        st.session_state.setdefault("profile", "typical")

    st.sidebar.divider()
    with st.sidebar.expander("About this data"):
        st.markdown(
            f"Rule corpus `{health['corpus_version']}` — {health['corpus_chunks']} entries.\n\n"
            "**Not verified against current IRDAI circulars.** Figures estimate likely "
            "deductions under configurable insurer rules; they do not predict a specific "
            "adjudicator's decision.\n\nSample claims are synthetic. Uploaded documents "
            "are processed in memory and not stored."
        )
