"""Portfolio aggregation + natural-language querying over the claims database.

This is the layer that turns a per-claim checker into decision support: which
billing habits leak the most money across the whole book, not just this bill.
"""

from __future__ import annotations

import re
import sqlite3
import time
from decimal import Decimal

import pandas as pd
from pydantic import BaseModel

from claimiq.llm import LLMClient, try_client
from claimiq.store import claims_df, doc_gaps_df, findings_df, scoped_connection


def _sum(column) -> Decimal:
    """Exact total of a Decimal column.

    pandas `.sum()` on an empty object column returns the integer 0 rather than a
    Decimal, and on a non-empty one it accumulates left-to-right with `+`, which is
    exact for Decimals. Both cases are funnelled through here so callers never have to
    know which one they got.
    """
    total = column.sum() if len(column) else 0
    return total if isinstance(total, Decimal) else Decimal(str(total))


LIST_LABEL = {
    "LIST_I_OPTIONAL": "List I — optional (patient pays)",
    "LIST_II_ROOM": "List II — subsumed in room (hospital absorbs)",
    "LIST_III_PROCEDURE": "List III — subsumed in procedure (hospital absorbs)",
    "LIST_IV_TREATMENT": "List IV — subsumed in treatment (hospital absorbs)",
}


def portfolio_summary(tenant: str | None = None) -> dict:
    claims = claims_df(tenant)
    findings = findings_df(tenant)
    gaps = doc_gaps_df(tenant)

    if claims.empty:
        return {"empty": True}

    hospital = findings[findings.bearer == "HOSPITAL"]

    # Every figure here is a Decimal sum serialised as a string. It used to be
    # float(...) on all seven, which is how the module that exists to report exact
    # money ended up being the one place it stopped being exact. JSON has no decimal
    # type, so strings are what survive the API boundary -- the same choice the
    # ClaimPacket models already make.
    gross = _sum(claims.gross_bill)
    settlement = _sum(claims.settlement)

    return {
        "empty": False,
        "claims": len(claims),
        "ai_claims": int(claims.ai_pipeline.sum()),
        "gross": str(gross),
        "settlement": str(settlement),
        "patient": str(_sum(claims.patient_liability)),
        "hospital_writeoff": str(_sum(claims.hospital_writeoff)),
        "room_rent_deduction": str(_sum(claims.room_rent_deduction)),
        "preventable": str(_sum(hospital.deducted)),
        "avg_deduction_pct": str(
            ((Decimal("1") - settlement / gross) * 100).quantize(Decimal("0.1"))
            if gross
            else Decimal("0")
        ),
        "unmapped_total": int(claims.unmapped_count.sum()),
        "doc_gaps": len(gaps),
    }


def leakage_by_cause(tenant: str | None = None) -> pd.DataFrame:
    """Where the money goes, split by who absorbs it."""
    claims = claims_df(tenant)
    findings = findings_df(tenant)
    if claims.empty:
        return pd.DataFrame(columns=["cause", "amount", "bearer"])

    rows = []
    for classification, label in LIST_LABEL.items():
        subset = findings[findings.classification == classification]
        if (deducted := _sum(subset.deducted)) > 0:
            rows.append(
                {
                    "cause": label,
                    "amount": deducted,
                    "bearer": "PATIENT" if classification == "LIST_I_OPTIONAL" else "HOSPITAL",
                }
            )

    room = _sum(claims.room_rent_deduction)
    if room > 0:
        rows.append({"cause": "Room rent cap + proportionate", "amount": room, "bearer": "PATIENT"})

    other_policy = (
        _sum(claims.patient_liability)
        - room
        - _sum(findings[findings.classification == "LIST_I_OPTIONAL"].deducted)
    )
    if other_policy > 0:
        rows.append(
            {"cause": "Co-pay, deductible and sub-limits", "amount": other_policy, "bearer": "PATIENT"}
        )

    return pd.DataFrame(rows).sort_values("amount", ascending=False).reset_index(drop=True)


def top_leaking_items(limit: int = 10, tenant: str | None = None) -> pd.DataFrame:
    findings = findings_df(tenant)
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


def top_missing_documents(limit: int = 10, tenant: str | None = None) -> pd.DataFrame:
    gaps = doc_gaps_df(tenant)
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

# Views, not base tables. Money is stored as integer paise for exactness; these
# expose rupees so generated SQL reads naturally and answers come back as 24300
# rather than 2430000.
SCHEMA_DESCRIPTION = """
View v_claims(claim_id TEXT, audited_at TEXT, month TEXT, diagnosis TEXT, procedure TEXT,
  gross_bill REAL, settlement REAL, patient_liability REAL, hospital_writeoff REAL,
  room_rent_deduction REAL, unmapped_count INT, doc_gap_count INT, ai_pipeline INT)
  All money columns are in rupees. month is 'YYYY-MM'.
  ai_pipeline = 1 when the claim went through the full LLM agent, 0 when deterministic.

View v_findings(claim_id TEXT, line_no INT, description TEXT, head TEXT,
  classification TEXT, bearer TEXT, amount REAL, deducted REAL, cited_chunk_id TEXT)
  classification in (PAYABLE, LIST_I_OPTIONAL, LIST_II_ROOM, LIST_III_PROCEDURE,
                     LIST_IV_TREATMENT, UNMAPPED)
  bearer in (INSURER, PATIENT, HOSPITAL, UNKNOWN)
  bearer='HOSPITAL' with deducted>0 is preventable hospital loss -- a billing error.

Table doc_gaps(claim_id TEXT, document_id TEXT, name TEXT, severity TEXT)
  severity in (BLOCKER, WARNING, INFO)
"""

SQL_SYSTEM = f"""You translate questions about a hospital claim-audit database into SQLite.

{SCHEMA_DESCRIPTION}

Rules:
- Emit exactly one SELECT statement. No INSERT/UPDATE/DELETE/DROP/ATTACH/PRAGMA.
- Only the three tables above.
- Always LIMIT to at most 50 rows.
- Money columns are rupees. Round aggregates to 0 decimals.
- Alias aggregates to readable names."""

# Views only for money-bearing data, so generated SQL cannot accidentally read raw
# paise and report a figure 100x too large.
ALLOWED_TABLES = {"v_claims", "v_findings", "doc_gaps"}
FORBIDDEN = re.compile(
    r"(?is)\b(drop|delete|insert|update|alter|create|attach|detach|vacuum|pragma|reindex|replace|claims|findings)\b"
)

# Rows a generated query may return, and how long it may run. Both are enforced here
# rather than asked for in the prompt: SQL_SYSTEM tells the model to LIMIT 50, and a
# model following an instruction is not a resource control. A cartesian join across
# v_claims and v_findings is one token away at all times.
MAX_ROWS = 200
QUERY_TIMEOUT_S = 5.0

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_STRINGS = re.compile(r"'(?:[^']|'')*'")


class SQLAnswer(BaseModel):
    sql: str
    explanation: str


def _analysable(sql: str) -> str:
    """The query with comments removed and string literals blanked.

    Both matter, and for different reasons.

    Comments hide keywords from a keyword check -- `SELECT 1 /*`, `DROP`, `*/` reads as
    harmless to a regex scanning the raw text. Strings cause the opposite failure: a
    perfectly legitimate `WHERE description LIKE '%drop foot%'` trips the FORBIDDEN
    list and the user is told their question is dangerous. Blanking rather than
    deleting keeps offsets stable so error positions still make sense.
    """
    without_comments = _COMMENTS.sub(" ", sql)
    return _STRINGS.sub(lambda m: "'" + " " * (len(m.group()) - 2) + "'", without_comments)


def validate_sql(sql: str) -> str:
    """Reject anything that is not a single read-only SELECT, then bound it.

    Guardrails are structural, not prompt-based: the model being well-behaved is
    not a security control. Neither is the model being *correct* -- this also has to
    hold when the question is adversarial, because /api/analytics/ask takes free text
    from whoever can reach the endpoint and feeds it to something that writes SQL.
    """
    cleaned = sql.strip().rstrip(";").strip()
    probe = _analysable(cleaned)

    if not probe.strip().lower().startswith(("select", "with")):
        raise ValueError("only SELECT queries are allowed")
    if ";" in probe:
        raise ValueError("multiple statements are not allowed")
    if FORBIDDEN.search(probe):
        raise ValueError("query contains a forbidden keyword")

    # CTE names are defined by the query itself, so they are legal targets of FROM/JOIN
    # even though they are not tables. Without this, every `WITH monthly AS (...)`
    # query -- which is what the model reaches for on any trend question -- was
    # rejected as referencing an unknown table called `monthly`.
    defined = {
        name.lower()
        for name in re.findall(r"(?:\bwith\b|,)\s*([a-zA-Z_]\w*)\s+as\s*\(", probe, re.IGNORECASE)
    }
    referenced = set()
    for clause in re.findall(
        r"\b(?:from|join)\s+([a-zA-Z_]\w*(?:\s*,\s*[a-zA-Z_]\w*)*)",
        probe,
        re.IGNORECASE,
    ):
        referenced.update(t.strip().lower() for t in clause.split(","))
    unknown = referenced - ALLOWED_TABLES - defined
    if unknown:
        raise ValueError(f"unknown table(s): {', '.join(sorted(unknown))}")

    # A LIMIT the model chose is a suggestion; this is the ceiling. Appending is safe
    # because the statement is known single and known to end here.
    if not re.search(r"\blimit\s+\d+\s*$", probe, re.IGNORECASE):
        cleaned = f"{cleaned}\nLIMIT {MAX_ROWS}"
    return cleaned


def _run_bounded(
    sql: str, timeout_s: float = QUERY_TIMEOUT_S, tenant: str | None = None
) -> pd.DataFrame:
    """Execute read-only, scoped to one tenant, with a wall-clock ceiling.

    SQLite has no statement timeout, so the interrupt goes through a progress handler:
    the callback fires every N VDBE instructions and returning non-zero aborts the
    statement. This is the only mechanism that stops a query that is *making progress*
    but will not finish this decade -- a busy_timeout does not help, because nothing
    is blocked.

    Scoping happens in `scoped_connection`, not here and not in the SQL. The generated
    query mentions no tenant and cannot be made to: the views it reads are temp views
    that only contain this tenant's rows. See `store.scoped_connection`.
    """
    deadline = time.monotonic() + timeout_s

    with scoped_connection(tenant) as conn:
        conn.execute("PRAGMA query_only = ON")
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        try:
            frame = pd.read_sql_query(sql, conn)
        except sqlite3.OperationalError as exc:
            if "interrupt" in str(exc).lower():
                raise ValueError(
                    f"the generated query was still running after {timeout_s:g}s and was "
                    "stopped. Try a narrower question."
                ) from exc
            raise ValueError(f"SQLite rejected the generated query: {exc}") from exc
        except sqlite3.Error as exc:
            raise ValueError(f"SQLite rejected the generated query: {exc}") from exc
        finally:
            conn.set_progress_handler(None, 0)

    return frame.head(MAX_ROWS)


def ask(
    question: str, client: LLMClient | None = None, tenant: str | None = None
) -> tuple[str, pd.DataFrame, str]:
    """Natural language -> validated SQL -> dataframe."""
    if len(question) > 500:
        # Long free text into a prompt that writes SQL is where injection lives. It is
        # not a complete defence -- validate_sql is -- but there is no legitimate
        # 4,000-character question about this five-column schema.
        raise ValueError("question is too long; keep it under 500 characters")

    client = client or try_client()
    if client is None:
        raise RuntimeError("Natural-language querying needs an LLM. Set LLM_API_KEY.")

    answer = client.structured(
        node="text_to_sql", system=SQL_SYSTEM, user=question, schema=SQLAnswer
    )
    sql = validate_sql(answer.sql)
    return sql, _run_bounded(sql, tenant=tenant), answer.explanation
