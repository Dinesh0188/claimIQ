"""Shared theme, branding and API helpers for the Streamlit pages."""

from __future__ import annotations

import requests
import streamlit as st

API = "http://127.0.0.1:8000"

BRAND = "ClaimIQ"
TAGLINE = "Catch the deduction before the insurer does"

# Single source of truth for colour. Red is always money the hospital loses,
# blue is always money the patient bears, green is always money that settles.
INK = "#0f1c2e"
ACCENT = "#0d7a6f"
HOSPITAL = "#d1495b"
PATIENT = "#3d6fa5"
SETTLED = "#0d9488"

CSS = f"""
<style>
  .stApp {{ background: #f7f9fb; }}
  section[data-testid="stSidebar"] {{ background: {INK}; }}
  section[data-testid="stSidebar"] * {{ color: #dbe4ee !important; }}
  section[data-testid="stSidebar"] .stAlert p {{ color: #0f1c2e !important; }}

  .ciq-hero {{
     background: linear-gradient(120deg, {INK} 0%, #1b3a5c 55%, {ACCENT} 140%);
     border-radius: 14px; padding: 22px 26px; margin-bottom: 18px; color: #fff;
  }}
  .ciq-hero h1 {{ margin: 0; font-size: 30px; letter-spacing: -0.5px; color: #fff; }}
  .ciq-hero p  {{ margin: 6px 0 0; opacity: .82; font-size: 14px; }}
  .ciq-tags {{ margin-top: 14px; }}
  .ciq-tag {{
     display: inline-block; margin: 0 6px 6px 0; padding: 3px 11px;
     border-radius: 999px; font-size: 11.5px; font-weight: 600; letter-spacing: .2px;
     background: rgba(255,255,255,.13); border: 1px solid rgba(255,255,255,.22);
  }}
  .ciq-note {{
     border-left: 3px solid {ACCENT}; background: #fff; border-radius: 6px;
     padding: 10px 14px; font-size: 13px; color: #33475b; margin: 6px 0 14px;
  }}
  div[data-testid="stMetric"] {{
     background: #fff; border: 1px solid #e3e9f0; border-radius: 11px;
     padding: 14px 16px; box-shadow: 0 1px 2px rgba(16,32,52,.05);
  }}
  div[data-testid="stMetricValue"] {{ font-size: 25px; color: {INK}; }}
  div[data-testid="stMetricLabel"] p {{
     font-size: 11.5px; text-transform: uppercase; letter-spacing: .55px; color: #64748b;
  }}
  .ciq-loss div[data-testid="stMetricValue"] {{ color: {HOSPITAL}; }}
  h2, h3 {{ color: {INK}; }}
  .stTabs [data-baseweb="tab"] {{ font-weight: 600; }}
</style>
"""

TAGS = [
    "LangGraph agent",
    "Vision extraction",
    "Hybrid RAG · BM25 + dense + RRF",
    "Grounded citations",
    "Self-verifying",
    "Deterministic money math",
]


def api_get(path: str, **params):
    response = requests.get(f"{API}{path}", params=params, timeout=180)
    response.raise_for_status()
    return response.json()


def api_post(path: str, payload=None, **params):
    response = requests.post(f"{API}{path}", json=payload, params=params, timeout=300)
    if response.status_code >= 400:
        try:
            raise RuntimeError(response.json().get("detail", response.text))
        except ValueError:
            raise RuntimeError(response.text) from None
    return response.json()


def rupees(value) -> str:
    """Indian digit grouping — 12,34,567 not 1,234,567. A hospital desk reads lakhs."""
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


def hero(title: str, subtitle: str, tags: bool = False) -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    tag_html = (
        '<div class="ciq-tags">' + "".join(f'<span class="ciq-tag">{t}</span>' for t in TAGS) + "</div>"
        if tags
        else ""
    )
    st.markdown(
        f'<div class="ciq-hero"><h1>{title}</h1><p>{subtitle}</p>{tag_html}</div>',
        unsafe_allow_html=True,
    )


def note(text: str) -> None:
    st.markdown(f'<div class="ciq-note">{text}</div>', unsafe_allow_html=True)


def require_api() -> dict:
    try:
        return api_get("/health")
    except Exception:
        pass

    # Single-port hosts (Streamlit Cloud, HF Spaces) have no second process, so bring
    # the API up in-process. Locally this is a no-op -- start.ps1 already started it.
    try:
        from _boot import ensure_api

        if ensure_api():
            return api_get("/health")
    except Exception:  # noqa: BLE001
        pass

    st.markdown(CSS, unsafe_allow_html=True)
    st.error(
        f"Cannot reach the {BRAND} API at {API}.\n\n"
        "Start both services from the project root:\n\n"
        "```\n.\\start.ps1\n```"
    )
    st.stop()


def sidebar(health: dict) -> None:
    st.sidebar.markdown(
        f"### {BRAND}\n<span style='opacity:.7;font-size:12.5px'>{TAGLINE}</span>",
        unsafe_allow_html=True,
    )
    st.sidebar.divider()

    if health["key_present"] and health["ai_enabled"]:
        st.sidebar.success(f"AI pipeline on\n\n`{health['model']}`")
    else:
        st.sidebar.info("Deterministic mode — no LLM.\nAdd a key in `providers.json`.")

    for warning in health.get("model_warnings") or []:
        if "AI off" not in warning:
            st.sidebar.warning(warning)

    # Live provider switch. Free-tier quotas run out mid-demo; being able to fail over
    # without editing a file and restarting is the point of the provider abstraction.
    try:
        available = api_get("/api/providers")
    except Exception:  # noqa: BLE001
        available = []

    if len(available) > 1:
        names = [p["name"] for p in available]
        current = health.get("provider", names[0])
        chosen = st.sidebar.selectbox(
            "Provider", names, index=names.index(current) if current in names else 0
        )
        if chosen != current:
            api_post(f"/api/providers/{chosen}")
            st.rerun()

        picked = next(p for p in available if p["name"] == chosen)
        if not picked["has_vision"]:
            st.sidebar.caption(
                "No vision model on this provider — scanned bills unavailable, "
                "native PDFs still parse via the text layer."
            )

    st.sidebar.caption(
        f"Corpus `{health['corpus_version']}`  \n"
        f"{health['corpus_chunks']} chunks · dense retrieval "
        f"{'on' if health['dense_retrieval'] else 'off'}"
    )
    st.sidebar.divider()
    st.sidebar.warning(
        "**Unverified snapshot.** The rule corpus has not been checked against current "
        "IRDAI circulars. Output estimates likely deductions under configurable insurer "
        "rules; it does not predict any specific adjudicator's decision."
    )
    st.sidebar.caption("All data is SYNTHETIC — not real patient data.")
