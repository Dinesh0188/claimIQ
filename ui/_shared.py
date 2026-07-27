"""Theme, chrome and API helpers.

Design rule applied throughout: if a hospital billing clerk does not need an element
to do their job, it is not on screen. No gradient banners, no capability tags, no
paragraph explaining what the tool they already opened is for.
"""

from __future__ import annotations

import requests
import streamlit as st

API = "http://127.0.0.1:8000"
BRAND = "ClaimIQ"

# Colour carries meaning and nothing else uses these three:
#   red   = money the hospital loses      blue = money the patient bears
#   green = money that settles
INK = "#101828"
MUTED = "#667085"
LINE = "#e4e7ec"
ACCENT = "#0e7c6b"
HOSPITAL = "#d1495b"
PATIENT = "#3d6fa5"

# Scoped selectors only. An earlier version used
#   section[data-testid="stSidebar"] * { color: ... !important }
# which forced one colour onto every descendant including components that carry
# their own background -- that is what made the sidebar unreadable.
CSS = f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');

  html, body, [class*="css"] {{ font-family: 'Plus Jakarta Sans', system-ui, sans-serif; }}

  .stApp {{
    background:
      radial-gradient(1100px 500px at 8% -12%, #e0f2fe 0%, transparent 55%),
      radial-gradient(900px 460px at 96% 0%, #d9f5ee 0%, transparent 52%),
      #f6f8fb;
  }}
  .block-container {{ padding-top: 2.2rem; max-width: 1200px; }}

  /* ---- sidebar: scoped selectors only. A universal `*` rule with !important is
     what made this unreadable before -- it recoloured components that carry their
     own background. ---- */
  section[data-testid="stSidebar"] {{
    background: linear-gradient(180deg, #0b1220 0%, #101a2e 55%, #0d1a2b 100%);
    border-right: 1px solid rgba(255,255,255,.07);
  }}
  section[data-testid="stSidebar"] .stMarkdown p,
  section[data-testid="stSidebar"] .stMarkdown li,
  section[data-testid="stSidebar"] label {{ color: #cbd5e1; }}
  section[data-testid="stSidebar"] h1,
  section[data-testid="stSidebar"] h2,
  section[data-testid="stSidebar"] h3 {{ color: #fff; }}
  section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{ color: #8fa3bd; }}
  section[data-testid="stSidebar"] [data-testid="stExpander"] details {{
    background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.09);
    border-radius: 10px;
  }}
  section[data-testid="stSidebar"] [data-testid="stExpander"] summary {{ color: #e2e8f0; }}

  .ciq-brand {{
    font-size: 27px; font-weight: 800; letter-spacing: -1px; line-height: 1.1;
    background: linear-gradient(92deg, #5eead4 0%, #38bdf8 48%, #a78bfa 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }}
  .ciq-brand-sub {{
    font-size: 10.5px; font-weight: 700; letter-spacing: 2.4px;
    text-transform: uppercase; color: #64809f; margin-top: 2px;
  }}

  .ciq-pill {{
    display: inline-flex; align-items: center; gap: 8px; margin-top: 14px;
    padding: 7px 13px; border-radius: 999px; font-size: 12px; font-weight: 700;
    background: rgba(94,234,212,.10); border: 1px solid rgba(94,234,212,.30);
    color: #7dd3c0;
  }}
  .ciq-dot {{
    width: 8px; height: 8px; border-radius: 50%; display: inline-block;
    box-shadow: 0 0 0 3px rgba(255,255,255,.07);
  }}

  h1.ciq-title {{
    font-size: 34px; font-weight: 800; letter-spacing: -1.1px; margin: 0 0 6px;
    background: linear-gradient(95deg, {INK} 30%, #1d6fa5 75%, {ACCENT} 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }}
  p.ciq-sub {{ color: {MUTED}; font-size: 15px; margin: 0 0 26px; font-weight: 500; }}

  /* ---- metrics ---- */
  div[data-testid="stMetric"] {{
    background: #fff; border: 1px solid {LINE}; border-radius: 16px;
    padding: 18px 20px; box-shadow: 0 1px 2px rgba(16,24,40,.05),
                                    0 12px 26px -20px rgba(16,24,40,.28);
    transition: transform .15s ease, box-shadow .15s ease;
  }}
  div[data-testid="stMetric"]:hover {{
    transform: translateY(-2px);
    box-shadow: 0 1px 2px rgba(16,24,40,.06), 0 18px 34px -20px rgba(16,24,40,.34);
  }}
  div[data-testid="stMetricValue"] {{
    font-size: 29px; font-weight: 800; letter-spacing: -.8px; color: {INK};
  }}
  div[data-testid="stMetricLabel"] p {{
    font-size: 10.5px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .9px; color: {MUTED};
  }}
  .ciq-loss div[data-testid="stMetric"] {{
    background: linear-gradient(160deg, #fff5f6 0%, #ffffff 62%);
    border-color: #fbd0d5;
  }}
  .ciq-loss div[data-testid="stMetricValue"] {{ color: {HOSPITAL}; }}

  /* ---- uploaded file rows ---- */
  .ciq-file {{
    display: flex; justify-content: space-between; align-items: center; gap: 12px;
    padding: 12px 15px; border: 1px solid {LINE}; border-radius: 12px;
    background: #fff; margin-bottom: 8px; font-size: 13.5px; font-weight: 600;
    color: {INK}; box-shadow: 0 1px 2px rgba(16,24,40,.04);
  }}
  .ciq-file:hover {{ border-color: #b9e4da; }}
  .ciq-tag {{
    font-size: 10.5px; font-weight: 800; letter-spacing: .5px; text-transform: uppercase;
    padding: 4px 9px; border-radius: 999px;
    background: #e8f6f2; color: #0b6d5e; border: 1px solid #c3e9df;
  }}
  .ciq-meta {{ color: {MUTED}; font-weight: 500; font-size: 12.5px; }}

  /* ---- buttons ---- */
  .stButton button {{
    border-radius: 11px; font-weight: 700; letter-spacing: .1px;
    padding: .55rem 1.15rem; border: 1px solid {LINE}; transition: all .16s ease;
  }}
  .stButton button[kind="primary"] {{
    background: linear-gradient(96deg, {ACCENT} 0%, #0ea5a0 55%, #0891b2 100%);
    border: none; color: #fff;
    box-shadow: 0 8px 20px -10px rgba(14,124,107,.85);
  }}
  .stButton button[kind="primary"]:hover:enabled {{
    transform: translateY(-1px);
    box-shadow: 0 12px 26px -10px rgba(14,124,107,.95);
  }}
  .stDownloadButton button {{ border-radius: 11px; font-weight: 700; }}

  /* ---- upload dropzone ---- */
  [data-testid="stFileUploaderDropzone"] {{
    background: linear-gradient(158deg, #ffffff 0%, #f2fbf9 100%);
    border: 2px dashed #9fd8cc; border-radius: 16px; padding: 26px;
    transition: all .18s ease;
  }}
  [data-testid="stFileUploaderDropzone"]:hover {{
    border-color: {ACCENT}; background: linear-gradient(158deg, #ffffff 0%, #e9f7f4 100%);
  }}

  /* ---- tabs ---- */
  .stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid {LINE}; }}
  .stTabs [data-baseweb="tab"] {{
    font-weight: 700; font-size: 14px; color: {MUTED};
    border-radius: 10px 10px 0 0; padding: 9px 16px;
  }}
  .stTabs [aria-selected="true"] {{ color: {ACCENT} !important; background: #eefaf7; }}

  div[data-testid="stDataFrame"] {{ border: 1px solid {LINE}; border-radius: 12px; }}
  hr {{ border-color: {LINE}; }}

  /* ---- sample divider ---- */
  .ciq-or {{
    display: flex; align-items: center; gap: 14px; margin: 26px 0 12px;
    font-size: 12.5px; font-weight: 700; letter-spacing: .3px; color: {MUTED};
  }}
  .ciq-or::before, .ciq-or::after {{
    content: ""; flex: 1; height: 1px; background: {LINE};
  }}

  /* ---- value proposition, empty state only ---- */
  .ciq-value {{ margin-top: 26px; }}
  .ciq-value-head {{
    font-size: 21px; font-weight: 800; color: {INK};
    letter-spacing: -.5px; margin-bottom: 8px;
  }}
  .ciq-value p {{ color: {MUTED}; font-size: 14.5px; line-height: 1.65; max-width: 760px; }}
  .ciq-value-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 14px; margin: 22px 0 18px;
  }}
  .ciq-value-card {{
    background: #fff; border: 1px solid {LINE}; border-radius: 14px;
    padding: 18px 19px; border-top: 3px solid {LINE};
    box-shadow: 0 1px 2px rgba(16,24,40,.04), 0 14px 28px -24px rgba(16,24,40,.3);
  }}
  .ciq-vc-green {{ border-top-color: {ACCENT}; }}
  .ciq-vc-blue  {{ border-top-color: {PATIENT}; }}
  .ciq-vc-red   {{ border-top-color: {HOSPITAL}; background: linear-gradient(165deg,#fff6f7 0%,#fff 60%); }}
  .ciq-vc-label {{
    font-size: 11px; font-weight: 800; letter-spacing: .9px;
    text-transform: uppercase; color: {MUTED}; margin-bottom: 7px;
  }}
  .ciq-vc-text {{ font-size: 13.5px; line-height: 1.6; color: #475467; }}
  .ciq-value-foot {{ font-size: 13px !important; color: #7a889b !important; }}
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
    colour = "#22c55e" if live else "#f59e0b"
    label = "AI reading enabled" if live else "Deterministic mode"

    st.sidebar.markdown(
        '<div class="ciq-brand">ClaimIQ</div>'
        '<div class="ciq-brand-sub">Pre-submission audit</div>'
        f'<div class="ciq-pill"><span class="ciq-dot" style="background:{colour}"></span>'
        f"{label}</div>",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

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
    # one and imply certainty, the estimate is shown as a range and this selects which
    # interpretation the headline figure uses.
    try:
        profiles = api_get("/api/profiles")
        names = list(profiles)
        picked = st.sidebar.selectbox(
            "Insurer interpretation", names,
            index=names.index(st.session_state.get("profile", "typical"))
            if st.session_state.get("profile", "typical") in names
            else names.index("typical"),
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
