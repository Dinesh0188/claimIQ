"""Portfolio aggregation + natural-language querying over the claims database.

This is the layer that turns a per-claim checker into decision support: which
billing habits leak the most money across the whole book, not just this bill.
"""

from __future__ import annotations

import re
import sqlite3

import pandas as pd
from pydantic import BaseModel

from claimiq.llm import LLMClient, try_client
from claimiq.store import claims_df, connect, doc_gaps_df, findings_df

LIST_LABEL = {
    "LIST_I_OPTIONAL": "List I — optional (patient pays)",
    "LIST_II_ROOM": "List II — subsumed in room (hospital absorbs)",
    "LIST_III_PROCEDURE": "List III — subsumed in procedure (hospital absorbs)",
    "LIST_IV_TREATMENT": "List IV — subsumed in treatment (hospital absorbs)",
}


def portfolio_summary() -> dict:
    claims = claims_df()
    findings = findings_df()
    gaps = doc_gaps_df()

    if claims.empty:
        return {"empty": True}

    hospital = findings[findings.bearer == "HOSPITAL"]
    preventable = float(hospital.deducted.sum())

    return {
        "empty": False,
        "claims": len(claims),
        "ai_claims": int(claims.ai_pipeline.sum()),
        "gross": float(claims.gross_bill.sum()),
        "settlement": float(claims.settlement.sum()),
        "patient": float(claims.patient_liability.sum()),
        "hospital_writeoff": float(claims.hospital_writeoff.sum()),
        "room_rent_deduction": float(claims.room_rent_deduction.sum()),
        "preventable": preventable,
        "avg_deduction_pct": float(
            (1 - claims.settlement.sum() / claims.gross_bill.sum()) * 100
        ),
        "unmapped_total": int(claims.unmapped_count.sum()),
        "doc_gaps": len(gaps),
    }


def leakage_by_cause() -> pd.DataFrame:
    """Where the money goes, split by who absorbs it."""
    claims = claims_df()
    findings = findings_df()
    if claims.empty:
        return pd.DataFrame(columns=["cause", "amount", "bearer"])

    rows = []
    for classification, label in LIST_LABEL.items():
        subset = findings[findings.classification == classification]
        if subset.deducted.sum() > 0:
            rows.append(
                {
                    "cause": label,
                    "amount": float(subset.deducted.sum()),
                    "bearer": "PATIENT" if classification == "LIST_I_OPTIONAL" else "HOSPITAL",
                }
            )

    room = float(claims.room_rent_deduction.sum())
    if room > 0:
        rows.append({"cause": "Room rent cap + proportionate", "amount": room, "bearer": "PATIENT"})

    other_policy = float(
        claims.patient_liability.sum()
        - claims.room_rent_deduction.sum()
        - findings[findings.classification == "LIST_I_OPTIONAL"].deducted.sum()
    )
    if other_policy > 0:
        rows.append(
            {"cause": "Co-pay, deductible and sub-limits", "amount": other_policy, "bearer": "PATIENT"}
        )

    return pd.DataFrame(rows).sort_values("amount", ascending=False).reset_index(drop=True)


def top_leaking_items(limit: int = 10) -> pd.DataFrame:
    findings = findings_df()
    if findings.empty:
        return pd.DataFrame(columns=["description", "claims", "total_deducted"])

    hospital = findings[(findings.bearer == "HOSPITAL") & (findings.deducted > 0)].copy()
    if hospital.empty:
        return pd.DataFrame(columns=["description", "claims", "total_deducted"])

    hospital["item"] = hospital.description.str.lower().str.strip()
    grouped = (
        hospital.groupby("item")
        .agg(claims=("claim_id", "nunique"), total_deducted=("deducted", "sum"))
        .sort_values("total_deducted", ascending=False)
        .head(limit)
        .reset_index()
    )
    return grouped


def top_missing_documents(limit: int = 10) -> pd.DataFrame:
    gaps = doc_gaps_df()
    if gaps.empty:
        return pd.DataFrame(columns=["name", "severity", "claims"])
    return (
        gaps.groupby(["name", "severity"])
        .agg(claims=("claim_id", "nunique"))
        .sort_values("claims", ascending=False)
        .head(limit)
        .reset_index()
    )


# --- text to SQL ----------------------------------------------------------

SCHEMA_DESCRIPTION = """
Table claims(claim_id TEXT, audited_at TEXT, month TEXT, diagnosis TEXT, procedure TEXT,
  gross_bill REAL, settlement REAL, patient_liability REAL, hospital_writeoff REAL,
  room_rent_deduction REAL, unmapped_count INT, doc_gap_count INT, ai_pipeline INT)

Table findings(claim_id TEXT, line_no INT, description TEXT, head TEXT,
  classification TEXT, bearer TEXT, amount REAL, deducted REAL, cited_chunk_id TEXT)
  classification in (PAYABLE, LIST_I_OPTIONAL, LIST_II_ROOM, LIST_III_PROCEDURE,
                     LIST_IV_TREATMENT, UNMAPPED)
  bearer in (INSURER, PATIENT, HOSPITAL, UNKNOWN)

Table doc_gaps(claim_id TEXT, document_id TEXT, name TEXT, severity TEXT)
  severity in (BLOCKER, QUERY_LIKELY, ADVISORY)
"""

SQL_SYSTEM = f"""You translate questions about a hospital claim-audit database into SQLite.

{SCHEMA_DESCRIPTION}

Rules:
- Emit exactly one SELECT statement. No INSERT/UPDATE/DELETE/DROP/ATTACH/PRAGMA.
- Only the three tables above.
- Always LIMIT to at most 50 rows.
- Money columns are rupees. Round aggregates to 0 decimals.
- Alias aggregates to readable names."""

ALLOWED_TABLES = {"claims", "findings", "doc_gaps"}
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|attach|detach|pragma|create|replace|vacuum)\b",
    re.IGNORECASE,
)


class SQLAnswer(BaseModel):
    sql: str
    explanation: str


def validate_sql(sql: str) -> str:
    """Reject anything that is not a single read-only SELECT.

    Guardrails are structural, not prompt-based: the model being well-behaved is
    not a security control.
    """
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned.lower().startswith(("select", "with")):
        raise ValueError("only SELECT queries are allowed")
    if ";" in cleaned:
        raise ValueError("multiple statements are not allowed")
    if FORBIDDEN.search(cleaned):
        raise ValueError("query contains a forbidden keyword")

    referenced = set(re.findall(r"\b(?:from|join)\s+([a-zA-Z_][\w]*)", cleaned, re.IGNORECASE))
    unknown = {t.lower() for t in referenced} - ALLOWED_TABLES
    if unknown:
        raise ValueError(f"unknown table(s): {', '.join(sorted(unknown))}")
    return cleaned


def ask(question: str, client: LLMClient | None = None) -> tuple[str, pd.DataFrame, str]:
    """Natural language -> validated SQL -> dataframe."""
    client = client or try_client()
    if client is None:
        raise RuntimeError("Natural-language querying needs an LLM. Set LLM_API_KEY.")

    answer = client.structured(
        node="text_to_sql", system=SQL_SYSTEM, user=question, schema=SQLAnswer
    )
    sql = validate_sql(answer.sql)

    with connect() as conn:
        conn.execute("PRAGMA query_only = ON")
        try:
            frame = pd.read_sql_query(sql, conn)
        except sqlite3.Error as exc:
            raise ValueError(f"SQLite rejected the generated query: {exc}") from exc

    return sql, frame, answer.explanation
