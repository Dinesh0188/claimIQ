"""Theme, chrome and API helpers.

Dark surface, single orange accent. Colour carries meaning and is not decoration:
orange is the product, red is money the hospital loses, blue is money the patient
bears, green is money that settles. Nothing else is coloured.
"""

from __future__ import annotations

from collections import Counter
from html import escape

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

  /* ============================================================
     TOKENS
     One scale each for space, type and radius. Previously there were
     24 spacing literals, 13 font sizes (six of them fractional) and
     8 radii, all typed at the point of use -- so nothing lined up
     with anything and every new element invented its own numbers.
     ============================================================ */
  :root {{
    --ink: {INK};
    --panel: {PANEL};
    --panel-2: #101216;
    --line: {LINE};
    --text: {TEXT};
    --muted: {MUTED};
    /* Was #6d7480 at 10px -- 3.6:1, below AA. Lightened to clear 4.5:1. */
    --muted-2: #8d95a1;
    --accent: {ACCENT};
    --accent-deep: {ACCENT_DEEP};
    --accent-soft: #ffb277;
    --hospital: {HOSPITAL};
    --patient: {PATIENT};
    --settled: {SETTLED};

    --s1: 4px;  --s2: 8px;  --s3: 12px; --s4: 16px;
    --s5: 24px; --s6: 32px; --s7: 48px;

    --t-xs: 11px; --t-sm: 12px; --t-md: 14px;
    --t-lg: 16px; --t-xl: 20px; --t-2xl: 26px; --t-3xl: 34px;

    --r-sm: 8px; --r-md: 12px; --r-lg: 16px; --r-pill: 999px;
  }}

  html, body, [class*="css"] {{ font-family: 'Plus Jakarta Sans', system-ui, sans-serif; }}

  .stApp {{
    background:
      radial-gradient(900px 420px at 12% -10%, rgba(255,122,26,.13) 0%, transparent 60%),
      radial-gradient(700px 380px at 92% 4%, rgba(255,122,26,.07) 0%, transparent 55%),
      var(--ink);
    color: var(--text);
  }}
  .block-container {{ padding-top: var(--s6); max-width: 1240px; }}

  h1, h2, h3, h4, p, span, label, li {{ color: var(--text); }}
  [data-testid="stCaptionContainer"] p {{ color: var(--muted); }}

  /* Keyboard focus. The primary button restyles box-shadow for its glow, which
     overwrote Streamlit's focus ring -- so tabbing to "Run audit" looked identical
     to hovering it. This wins because it is declared later and uses a colour that
     is not in the hover state. */
  :focus-visible {{
    outline: 2px solid var(--accent) !important;
    outline-offset: 2px !important;
    border-radius: var(--r-sm);
  }}

  @media (prefers-reduced-motion: reduce) {{
    * {{ transition: none !important; animation: none !important; }}
    .stButton button:hover:enabled,
    div[data-testid="stMetric"]:hover {{ transform: none !important; }}
  }}

  /* ---------- sidebar ---------- */
  section[data-testid="stSidebar"] {{
    background: #0a0b0e; border-right: 1px solid var(--line);
  }}
  section[data-testid="stSidebar"] .stMarkdown p,
  section[data-testid="stSidebar"] li,
  section[data-testid="stSidebar"] label {{ color: #c7ccd4; }}
  section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{ color: var(--muted-2); }}
  section[data-testid="stSidebar"] [data-testid="stExpander"] details {{
    background: var(--panel); border: 1px solid var(--line); border-radius: var(--r-md);
  }}
  section[data-testid="stSidebar"] [data-testid="stExpander"] summary {{ color: var(--text); }}

  section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"] {{
    border-radius: var(--r-sm); margin-bottom: 2px;
  }}
  section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"]:hover {{
    background: rgba(255,122,26,.10);
  }}
  section[data-testid="stSidebar"] a[aria-current="page"] {{
    background: rgba(255,122,26,.16) !important;
    box-shadow: inset 2px 0 0 var(--accent);
  }}

  /* Solid colour, not background-clip. `color: transparent` makes the wordmark and
     every page title vanish when the background does not paint -- print, high
     contrast mode, forced colours. A title that disappears on Ctrl+P is not a
     styling preference, it is a broken document. */
  .ciq-brand {{
    font-size: var(--t-2xl); font-weight: 800; letter-spacing: -1px; line-height: 1;
    color: var(--accent);
  }}
  .ciq-brand-sub {{
    font-size: var(--t-xs); font-weight: 700; letter-spacing: 2.4px;
    text-transform: uppercase; color: var(--muted-2); margin-top: var(--s1);
  }}
  .ciq-pill {{
    display: inline-flex; align-items: center; gap: var(--s2); margin-top: var(--s4);
    padding: var(--s2) var(--s3); border-radius: var(--r-pill);
    font-size: var(--t-xs); font-weight: 700;
    background: rgba(255,122,26,.11); border: 1px solid rgba(255,122,26,.30);
    color: #ffab6b;
  }}
  .ciq-dot {{ width: 7px; height: 7px; border-radius: 50%; display: inline-block; }}

  /* ---------- page header ---------- */
  h1.ciq-title {{
    font-size: var(--t-3xl); font-weight: 800; letter-spacing: -1.2px;
    margin: 0 0 var(--s2); color: var(--text);
  }}
  p.ciq-sub {{
    color: var(--muted); font-size: var(--t-lg); margin: 0 0 var(--s5); font-weight: 500;
  }}

  /* ============================================================
     VERDICT -- the one thing to read at a glance.
     Colour is never the only channel: each state carries a glyph
     and a word, so it survives greyscale printing and colour
     blindness. Before this existed a clean claim and a
     catastrophic one rendered identically and the user had to
     work the verdict out from four rupee figures.
     ============================================================ */
  .ciq-verdict {{
    display: flex; align-items: flex-start; gap: var(--s4);
    padding: var(--s4) var(--s5); border-radius: var(--r-lg);
    border: 1px solid var(--line); background: var(--panel);
    margin-bottom: var(--s4);
  }}
  .ciq-verdict-mark {{
    font-size: var(--t-xl); font-weight: 800; line-height: 1.2;
    font-family: 'JetBrains Mono', monospace; flex: 0 0 auto;
  }}
  .ciq-verdict-title {{
    font-size: var(--t-xl); font-weight: 800; letter-spacing: -.4px;
    margin-bottom: var(--s1);
  }}
  .ciq-verdict-detail {{ font-size: var(--t-md); color: var(--muted); line-height: 1.6; }}
  .ciq-v-clean {{
    border-color: rgba(61,220,132,.42);
    background: linear-gradient(168deg, rgba(61,220,132,.10) 0%, var(--panel) 70%);
  }}
  .ciq-v-clean .ciq-verdict-mark, .ciq-v-clean .ciq-verdict-title {{ color: var(--settled); }}
  .ciq-v-attention {{
    border-color: rgba(255,122,26,.45);
    background: linear-gradient(168deg, rgba(255,122,26,.10) 0%, var(--panel) 70%);
  }}
  .ciq-v-attention .ciq-verdict-mark,
  .ciq-v-attention .ciq-verdict-title {{ color: var(--accent); }}
  .ciq-v-unverified {{
    border-color: rgba(255,92,92,.45);
    background: linear-gradient(168deg, rgba(255,92,92,.10) 0%, var(--panel) 70%);
  }}
  .ciq-v-unverified .ciq-verdict-mark,
  .ciq-v-unverified .ciq-verdict-title {{ color: var(--hospital); }}

  /* Severity chip. Always renders the WORD, never a colour alone. */
  .ciq-sev {{
    display: inline-block; font-size: 10px; font-weight: 800; letter-spacing: .9px;
    text-transform: uppercase; padding: 3px var(--s2); border-radius: var(--r-sm);
    border: 1px solid currentColor; white-space: nowrap;
  }}
  .ciq-sev-blocker {{ color: var(--hospital); }}
  .ciq-sev-warning {{ color: var(--accent); }}
  .ciq-sev-info    {{ color: var(--muted); }}

  .ciq-cite {{
    font-family: 'JetBrains Mono', monospace; font-size: var(--t-xs);
    color: var(--accent-soft);
  }}
  .ciq-math {{
    font-family: 'JetBrains Mono', monospace; font-size: var(--t-sm);
    background: var(--panel-2); border: 1px solid var(--line);
    border-radius: var(--r-sm); padding: var(--s3); color: var(--text);
    white-space: pre-wrap; overflow-x: auto;
  }}

  /* ---------- metrics ---------- */
  div[data-testid="stMetric"] {{
    background: linear-gradient(168deg, var(--panel) 0%, var(--panel-2) 100%);
    border: 1px solid var(--line); border-radius: var(--r-lg);
    padding: var(--s4) var(--s4);
    transition: border-color .16s ease;
  }}
  div[data-testid="stMetricValue"] {{
    font-size: var(--t-2xl); font-weight: 800; letter-spacing: -.8px; color: var(--text);
  }}
  div[data-testid="stMetricLabel"] p {{
    font-size: var(--t-xs); font-weight: 700; text-transform: uppercase;
    letter-spacing: .9px; color: var(--muted);
  }}
  .ciq-loss div[data-testid="stMetric"] {{
    background: linear-gradient(168deg, rgba(255,92,92,.13) 0%, var(--panel) 70%);
    border-color: rgba(255,92,92,.34);
  }}
  .ciq-loss div[data-testid="stMetricValue"] {{ color: var(--hospital); }}

  /* ---------- uploaded files ---------- */
  .ciq-file {{
    display: flex; justify-content: space-between; align-items: center;
    gap: var(--s3); flex-wrap: wrap;
    padding: var(--s3) var(--s4); border: 1px solid var(--line);
    border-radius: var(--r-md); background: var(--panel);
    margin-bottom: var(--s2); font-size: var(--t-md); font-weight: 600;
    color: var(--text);
  }}
  .ciq-file:hover {{ border-color: rgba(255,122,26,.42); }}
  .ciq-tag {{
    font-size: 10px; font-weight: 800; letter-spacing: .6px; text-transform: uppercase;
    padding: 3px var(--s2); border-radius: var(--r-pill);
    background: rgba(255,122,26,.14); color: var(--accent-soft);
    border: 1px solid rgba(255,122,26,.30);
  }}
  .ciq-meta {{ color: var(--muted); font-weight: 500; font-size: var(--t-sm); }}

  /* ---------- buttons ---------- */
  .stButton button {{
    border-radius: var(--r-md); font-weight: 700; padding: var(--s2) var(--s4);
    background: var(--panel); color: var(--text); border: 1px solid var(--line);
    transition: all .16s ease;
  }}
  .stButton button:hover:enabled {{
    border-color: rgba(255,122,26,.55); color: var(--accent-soft);
  }}
  .stButton button[kind="primary"] {{
    background: linear-gradient(96deg, var(--accent) 0%, var(--accent-deep) 100%);
    border: none; color: #160c04;
    box-shadow: 0 10px 26px -12px rgba(255,122,26,.9);
  }}
  .stButton button[kind="primary"]:hover:enabled {{
    transform: translateY(-1px);
    box-shadow: 0 14px 32px -12px rgba(255,122,26,1);
  }}
  .stDownloadButton button {{ border-radius: var(--r-md); font-weight: 700; }}

  /* ---------- upload dropzone ---------- */
  [data-testid="stFileUploaderDropzone"] {{
    background: linear-gradient(160deg, rgba(255,122,26,.06) 0%, var(--panel) 70%);
    border: 2px dashed rgba(255,122,26,.42); border-radius: var(--r-lg);
    padding: var(--s5); transition: all .18s ease;
  }}
  [data-testid="stFileUploaderDropzone"]:hover {{
    border-color: var(--accent);
    background: linear-gradient(160deg, rgba(255,122,26,.11) 0%, var(--panel) 70%);
  }}
  [data-testid="stFileUploaderDropzone"] button {{
    background: rgba(255,122,26,.14); border: 1px solid rgba(255,122,26,.34);
    color: var(--accent-soft);
  }}

  /* ---------- inputs ---------- */
  .stTextInput input, .stNumberInput input,
  .stSelectbox div[data-baseweb="select"] > div,
  .stDateInput input {{
    background: var(--panel) !important; border-color: var(--line) !important;
    color: var(--text) !important;
  }}

  /* ---------- tabs / expanders ---------- */
  .stTabs [data-baseweb="tab-list"] {{ gap: var(--s1); border-bottom: 1px solid var(--line); }}
  .stTabs [data-baseweb="tab"] {{
    font-weight: 700; font-size: var(--t-md); color: var(--muted);
    border-radius: var(--r-md) var(--r-md) 0 0; padding: var(--s2) var(--s4);
  }}
  .stTabs [aria-selected="true"] {{
    color: var(--accent) !important; background: rgba(255,122,26,.10);
    box-shadow: inset 0 -2px 0 var(--accent);
  }}
  [data-testid="stExpander"] details {{
    background: var(--panel); border: 1px solid var(--line);
    border-radius: var(--r-md); margin-bottom: var(--s2);
  }}

  div[data-testid="stDataFrame"] {{ border: 1px solid var(--line); border-radius: var(--r-md); }}
  hr {{ border-color: var(--line); }}
  code {{ font-family: 'JetBrains Mono', monospace; color: var(--accent-soft); }}

  /* ---------- sample divider ---------- */
  .ciq-or {{
    display: flex; align-items: center; gap: var(--s4); margin: var(--s6) 0 var(--s3);
    font-size: var(--t-sm); font-weight: 700; letter-spacing: .5px; color: var(--muted);
    text-transform: uppercase;
  }}
  .ciq-or::before, .ciq-or::after {{
    content: ""; flex: 1; height: 1px; background: var(--line);
  }}

  /* ---------- value proposition, empty state only ---------- */
  .ciq-value {{ margin-top: var(--s6); }}
  .ciq-value-head {{
    font-size: var(--t-2xl); font-weight: 800; color: var(--text);
    letter-spacing: -.6px; margin-bottom: var(--s2);
  }}
  .ciq-value p {{
    color: var(--muted); font-size: var(--t-lg); line-height: 1.7; max-width: 780px;
  }}
  .ciq-value-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
    gap: var(--s3); margin: var(--s5) 0 var(--s4);
  }}
  .ciq-value-card {{
    background: var(--panel); border: 1px solid var(--line); border-radius: var(--r-lg);
    padding: var(--s4); border-top: 3px solid var(--line);
  }}
  .ciq-vc-green {{ border-top-color: var(--settled); }}
  .ciq-vc-blue  {{ border-top-color: var(--patient); }}
  .ciq-vc-red   {{
    border-top-color: var(--hospital);
    background: linear-gradient(168deg, rgba(255,92,92,.09) 0%, var(--panel) 70%);
  }}
  .ciq-vc-label {{
    font-size: var(--t-xs); font-weight: 800; letter-spacing: .9px;
    text-transform: uppercase; color: var(--muted); margin-bottom: var(--s2);
  }}
  .ciq-vc-text {{ font-size: var(--t-md); line-height: 1.65; color: #b6bcc6; }}
  .ciq-value-foot {{ font-size: var(--t-sm) !important; color: var(--muted-2) !important; }}

  /* ============================================================
     RESPONSIVE
     Streamlit's st.columns does not wrap -- it compresses. Four
     26px metric values in a 100px column on a tablet overflow.
     ============================================================ */
  @media (max-width: 1100px) {{
    :root {{ --t-3xl: 28px; --t-2xl: 22px; }}
    [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; }}
    [data-testid="stHorizontalBlock"] > div[data-testid="column"] {{
      min-width: 46% !important; flex: 1 1 46% !important;
    }}
  }}
  @media (max-width: 640px) {{
    :root {{ --t-3xl: 24px; --t-2xl: 19px; }}
    .block-container {{ padding-left: var(--s3); padding-right: var(--s3); }}
    [data-testid="stHorizontalBlock"] > div[data-testid="column"] {{
      min-width: 100% !important; flex: 1 1 100% !important;
    }}
    .ciq-verdict {{ flex-direction: column; gap: var(--s2); }}
  }}

  /* ============================================================
     PRINT
     Billing staff attach these to appeals, so this is a real
     output format, not an afterthought. Dark theme inverted,
     chrome dropped, findings forced open, nothing page-broken
     mid-finding.
     ============================================================ */
  @media print {{
    @page {{ margin: 14mm; }}

    .stApp, .stApp > * {{ background: #fff !important; }}
    html, body, .stApp, p, span, li, td, th, label,
    h1, h2, h3, h4, .ciq-verdict-detail, .ciq-vc-text {{
      color: #000 !important;
    }}
    [data-testid="stCaptionContainer"] p, .ciq-meta {{ color: #444 !important; }}

    /* Chrome that means nothing on paper. */
    section[data-testid="stSidebar"],
    [data-testid="stToolbar"],
    [data-testid="stFileUploaderDropzone"],
    [data-testid="stHeader"],
    .stButton, .stDownloadButton, .ciq-or {{ display: none !important; }}

    .block-container {{ max-width: 100%; padding: 0 !important; }}

    /* An expander the reader cannot click has to be open. */
    [data-testid="stExpander"] details {{
      border: 1px solid #bbb !important; break-inside: avoid;
    }}
    [data-testid="stExpander"] details > div {{ display: block !important; }}

    .ciq-verdict, .ciq-value-card, div[data-testid="stMetric"] {{
      border: 1px solid #999 !important; break-inside: avoid;
      background: #fff !important;
    }}
    .ciq-verdict-mark, .ciq-verdict-title,
    .ciq-loss div[data-testid="stMetricValue"] {{ color: #000 !important; }}
    .ciq-sev {{ border: 1px solid #000 !important; color: #000 !important; }}
    .ciq-math {{
      background: #f4f4f4 !important; border: 1px solid #bbb !important;
      color: #000 !important;
    }}
    .ciq-cite {{ color: #333 !important; }}
    a[href]::after {{ content: " (" attr(href) ")"; font-size: 10px; color: #555; }}
  }}
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


# Re-exported from the engine so the screen and the arithmetic round the same way.
# The local copy used to do int(round(float(value))) -- float conversion plus banker's
# rounding, i.e. a different rule from the engine's ROUND_HALF_UP, applied to a value
# it had already made inexact.
from claimiq.money import ROUNDING_NOTE, rupees  # noqa: E402,F401


def page_header(title: str, subtitle: str) -> None:
    st.markdown(
        f'<h1 class="ciq-title">{escape(title)}</h1>'
        f'<p class="ciq-sub">{escape(subtitle)}</p>',
        unsafe_allow_html=True,
    )


# --- findings presentation -------------------------------------------------
#
# Shared because the audit view and the trace view must not disagree about what
# "BLOCKER" looks like or which finding is more urgent.

SEVERITY_ORDER = {"BLOCKER": 0, "WARNING": 1, "INFO": 2}

# A mark, not an emoji. Rendered in the monospace face so all three occupy the same
# width, and paired with the severity word everywhere it appears -- the old
# 🔴 / 🟠 / ⚪ differed only in hue, which is unreadable in greyscale print and for
# the ~8% of men with red-green colour blindness.
SEVERITY_MARK = {"BLOCKER": "[!]", "WARNING": "[*]", "INFO": "[i]"}

VERDICT_STYLE = {
    "CLEAN": ("ciq-v-clean", "[OK]", "Ready to submit"),
    "NEEDS_ATTENTION": ("ciq-v-attention", "[!]", "Needs attention"),
    "CANNOT_VERIFY": ("ciq-v-unverified", "[?]", "Could not fully check this claim"),
}


def severity_chip(severity: str) -> str:
    """Inline HTML chip. Always carries the word, never colour alone."""
    css = f"ciq-sev-{severity.lower()}"
    return f'<span class="ciq-sev {css}">{escape(severity)}</span>'


def verdict_banner(result: dict) -> None:
    """The one thing to read at a glance.

    Before this existed the user had to derive the verdict from four rupee figures,
    and "Checks FAILED" was the third clause of a grey caption underneath them.
    """
    verdict = result.get("verdict", "NEEDS_ATTENTION")
    css, mark, title = VERDICT_STYLE.get(verdict, VERDICT_STYLE["NEEDS_ATTENTION"])
    findings = collect_findings(result)
    counts = Counter(f["severity"] for f in findings)

    if verdict == "CLEAN":
        detail = (
            "Every line matched a rule, all required documents are attached, and the "
            "consistency checks passed."
        )
    elif verdict == "CANNOT_VERIFY":
        detail = " ".join(result.get("caveats") or []) or (
            "Internal checks disagreed with the figures below."
        )
    else:
        parts = [f"{counts[s]} {s.lower()}" for s in ("BLOCKER", "WARNING", "INFO") if counts[s]]
        # Only tell them to resolve blockers when there are blockers. The instruction
        # used to be unconditional, so a claim carrying one advisory note read
        # "1 info. Resolve blockers before submitting" -- an order to fix nothing.
        if counts["BLOCKER"]:
            action = "Resolve the blockers before submitting"
        elif counts["WARNING"]:
            action = "None of these block submission, but each one is money"
        else:
            action = "Nothing here blocks submission"
        detail = (
            f"{', '.join(parts)}. {action} — every finding below cites the rule it "
            f"came from."
        )

    st.markdown(
        f'<div class="ciq-verdict {css}">'
        f'<div class="ciq-verdict-mark">{mark}</div>'
        f"<div><div class='ciq-verdict-title'>{escape(title)}</div>"
        f"<div class='ciq-verdict-detail'>{escape(detail)}</div></div></div>",
        unsafe_allow_html=True,
    )


def collect_findings(result: dict) -> list[dict]:
    """Item verdicts, document gaps and consistency flags as one sorted list.

    The engine exposes three lists because they are computed by three different
    nodes. A user does not care which node found the problem, only how bad it is --
    so they are merged here and ordered by severity, then by rupee impact.
    """
    merged: list[dict] = []

    for f in result.get("findings") or []:
        if f["classification"] == "PAYABLE":
            continue  # not a finding: it is the absence of one
        merged.append(
            {
                **f,
                "kind": "item",
                "title": f["description"],
                "detail": f["reason"],
            }
        )
    for g in result.get("document_gaps") or []:
        merged.append({**g, "kind": "document", "title": g["name"], "detail": g["reason"]})
    for c in result.get("consistency_flags") or []:
        merged.append(
            {**c, "kind": "check", "title": c["check_id"].replace("CHK-", "").replace("-", " ").title(),
             "detail": c["message"]}
        )

    def rank(f: dict) -> tuple[int, float]:
        impact = f.get("impact")
        return (SEVERITY_ORDER.get(f["severity"], 3), -float(impact or 0))

    return sorted(merged, key=rank)


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
