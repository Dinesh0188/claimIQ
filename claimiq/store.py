"""SQLite persistence for the portfolio dashboard.

Three tables, plain sqlite3, no ORM. The dashboard aggregates a few hundred rows;
anything heavier here would be weight without benefit. See DECISIONS.md for why this
is not Postgres.

**Money is stored as integer paise, never REAL.** The engine is Decimal end to end and
the README makes a point of it, so persisting to a float would throw that away at the
last boundary. At this row count the drift would be invisible -- roughly 1e-10 on an
aggregate -- so this is a consistency fix rather than a live bug, but "money is never
float" should be true everywhere or it should not be claimed.

Rupee-denominated VIEWs sit on top. Reporting and the text-to-SQL feature read those,
so generated SQL keeps rupee semantics and answers come back as 24300 rather than
2430000, while the stored value stays exact.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pandas as pd

from claimiq.config import ROOT
from claimiq.money import from_paise, to_paise
from claimiq.state import AuditResult

DB_PATH = ROOT / "data" / "claimiq.db"

# Bumped when the schema changes shape. A stale database is dropped and rebuilt rather
# than migrated: every row is reproducible from data/generated/portfolio via seed_db.py,
# so a migration path would be ceremony for data that is regenerated in 30 seconds.
#
# That reasoning holds for THIS database and explicitly not for data/ledger.db, whose
# entire value is that it was never rebuilt. If this store ever stops being
# regenerable, drop-and-rebuild has to be replaced by real migrations.
#
# 3: rupee views stopped dividing by 100.0 (REAL) and now expose the integer paise
#    columns alongside, so readers can reassemble exact Decimals.
# 4: tenant on every table, and it is part of the primary key. Two hospitals both
#    numbering a claim CLM-001 previously collided on a global claim_id -- the second
#    save silently deleted the first tenant's row, which is data loss across a
#    security boundary rather than a duplicate-key error anyone would notice.
SCHEMA_VERSION = 4

# The tenant every row gets when nobody said otherwise: the local single-user demo,
# the seed script, and `python -m claimiq.graph`. Named rather than "" so that a row
# with no tenant is impossible and the column can be NOT NULL.
DEFAULT_TENANT = "local"

# How long a writer waits for the lock before giving up. Long enough to absorb a batch
# worker's commit, short enough that a genuinely wedged connection still surfaces.
BUSY_TIMEOUT_S = 10.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    tenant                    TEXT NOT NULL DEFAULT 'local',
    claim_id                  TEXT NOT NULL,
    audited_at                TEXT NOT NULL DEFAULT (datetime('now')),
    month                     TEXT NOT NULL,
    diagnosis                 TEXT,
    procedure                 TEXT,
    gross_bill_paise          INTEGER NOT NULL,
    settlement_paise          INTEGER NOT NULL,
    patient_liability_paise   INTEGER NOT NULL,
    hospital_writeoff_paise   INTEGER NOT NULL,
    room_rent_deduction_paise INTEGER NOT NULL DEFAULT 0,
    unmapped_count            INTEGER NOT NULL DEFAULT 0,
    doc_gap_count             INTEGER NOT NULL DEFAULT 0,
    corpus_version            TEXT,
    -- TRUE when this row came through the full LLM agent, FALSE for the
    -- deterministic-only path. The dashboard discloses the split rather than
    -- implying every row saw the model.
    ai_pipeline               INTEGER NOT NULL DEFAULT 0,
    -- Tenant first, and it is the leading column of the key rather than a filter
    -- bolted on afterwards. Every query that forgets the tenant then reads wrong
    -- rather than reading someone else's rows.
    PRIMARY KEY (tenant, claim_id)
);

CREATE TABLE IF NOT EXISTS findings (
    tenant         TEXT NOT NULL DEFAULT 'local',
    claim_id       TEXT NOT NULL,
    line_no        INTEGER NOT NULL,
    description    TEXT NOT NULL,
    head           TEXT,
    classification TEXT NOT NULL,
    bearer         TEXT NOT NULL,
    amount_paise   INTEGER NOT NULL,
    deducted_paise INTEGER NOT NULL,
    cited_chunk_id TEXT,
    PRIMARY KEY (tenant, claim_id, line_no),
    -- Composite, so a child can never be adopted by another tenant's parent of the
    -- same claim id. A foreign key on claim_id alone would have permitted exactly that.
    FOREIGN KEY (tenant, claim_id) REFERENCES claims(tenant, claim_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS doc_gaps (
    tenant      TEXT NOT NULL DEFAULT 'local',
    claim_id    TEXT NOT NULL,
    document_id TEXT NOT NULL,
    name        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    PRIMARY KEY (tenant, claim_id, document_id),
    FOREIGN KEY (tenant, claim_id) REFERENCES claims(tenant, claim_id) ON DELETE CASCADE
);

-- Precautionary at 302 rows, where SQLite scans the whole table faster than it can
-- consult an index. They earn their place from roughly 10k findings onward, which is
-- ~500 claims -- close enough to the demo size to be worth having already.
--
-- Each leads with `tenant`, which is what makes them usable at all once the data is
-- partitioned: an index on classification alone would be scanned in full and then
-- filtered, so the cost of a query would grow with every OTHER tenant's volume.
CREATE INDEX IF NOT EXISTS idx_findings_classification ON findings(tenant, classification);
CREATE INDEX IF NOT EXISTS idx_findings_bearer         ON findings(tenant, bearer);
CREATE INDEX IF NOT EXISTS idx_claims_month            ON claims(tenant, month);
CREATE INDEX IF NOT EXISTS idx_doc_gaps_severity       ON doc_gaps(tenant, severity);

-- Rupee views. Storage stays exact; readers get natural units.
--
-- These used to divide by 100.0, which is REAL division in SQLite -- so the module
-- that exists to keep money out of floating point handed out floats through its only
-- read path, and every dashboard and text-to-SQL figure inherited them. Integer
-- division by 100 keeps the whole-rupee column exact; `paise` carries the remainder,
-- and `rupees_df()` reassembles both into Decimal before anything arithmetic happens.
--
-- These are the UNSCOPED views, spanning every tenant. Generated SQL never reaches
-- them directly in a multi-tenant deployment: `scoped_connection()` installs TEMP
-- views of the same names, which SQLite resolves first. See that function for why.
CREATE VIEW IF NOT EXISTS v_claims AS
SELECT tenant, claim_id, audited_at, month, diagnosis, procedure,
       gross_bill_paise          / 100 AS gross_bill,
       settlement_paise          / 100 AS settlement,
       patient_liability_paise   / 100 AS patient_liability,
       hospital_writeoff_paise   / 100 AS hospital_writeoff,
       room_rent_deduction_paise / 100 AS room_rent_deduction,
       gross_bill_paise, settlement_paise, patient_liability_paise,
       hospital_writeoff_paise, room_rent_deduction_paise,
       unmapped_count, doc_gap_count, corpus_version, ai_pipeline
FROM claims;

CREATE VIEW IF NOT EXISTS v_findings AS
SELECT tenant, claim_id, line_no, description, head, classification, bearer,
       amount_paise   / 100 AS amount,
       deducted_paise / 100 AS deducted,
       amount_paise, deducted_paise,
       cited_chunk_id
FROM findings;
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_S)

    # SQLite silently ignores REFERENCES clauses unless this is on, per connection.
    # Declaring a foreign key without it is decoration.
    conn.execute("PRAGMA foreign_keys = ON")

    # Concurrency, which the single-user demo never exercised and the batch endpoint
    # does immediately. In the default rollback journal a writer blocks every reader,
    # so a dashboard query issued while a batch is persisting fails outright with
    # "database is locked" rather than waiting. WAL lets readers proceed against the
    # last committed snapshot while one writer appends; busy_timeout makes the second
    # concurrent *writer* wait its turn instead of erroring on contact.
    #
    # NORMAL rather than FULL synchronous: with WAL that is durable across process
    # crashes and only loses the last commits on OS/power failure, which for a
    # regenerable analytics store is the right trade.
    conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_S * 1000)}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")

    # 0 means either a brand-new file or a pre-versioning database -- both need the
    # rebuild, so this must not be written as `if version and ...`. Treating 0 as
    # falsy skips the migration on precisely the stale databases that require it,
    # and the failure surfaces later as an OperationalError on an INSERT.
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        conn.executescript(
            "DROP VIEW IF EXISTS v_findings; DROP VIEW IF EXISTS v_claims;"
            "DROP TABLE IF EXISTS doc_gaps; DROP TABLE IF EXISTS findings;"
            "DROP TABLE IF EXISTS claims;"
        )

    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


def scoped_connection(tenant: str | None) -> sqlite3.Connection:
    """A connection on which `v_claims` and `v_findings` mean *this tenant's* rows.

    The problem this solves is specific and otherwise nasty. `/api/analytics/ask`
    hands a model the schema and runs the SQL it writes. Adding a tenant column is no
    protection there at all -- the generated query simply will not mention it, and the
    answer silently spans every hospital in the database. Rewriting the model's SQL to
    inject a predicate is the usual reach and it is a parser problem in disguise:
    subqueries, CTEs, unions, correlated references, each a place to get it wrong once.

    So the scope moves underneath the query instead. SQLite resolves an unqualified
    name against the `temp` schema before `main`, so a TEMP VIEW named `v_claims`
    shadows the real one for this connection only. The generated SQL is untouched,
    knows nothing about tenants, and cannot address a row outside the scope -- because
    from where it sits those rows do not exist.

    The tenant is bound as a parameter... except that SQLite does not allow parameters
    in a view definition, so it is quoted instead. `_quote` doubles single quotes,
    which is the whole of SQLite's string escaping; the value comes from a parsed API
    key rather than from user input, so this is defence in depth on a value that
    already cannot be attacker-controlled.

    `tenant=None` means single-tenant: no temp views, the real ones are used, and the
    answer spans everything. That is correct for the local demo and is exactly what
    must not happen once keys are configured -- which is why the API derives this
    argument from the principal rather than from a query parameter.
    """
    conn = connect()
    if tenant is None:
        return conn

    scope = _quote(tenant)
    conn.executescript(
        f"""
        CREATE TEMP VIEW v_claims   AS SELECT * FROM main.v_claims   WHERE tenant = {scope};
        CREATE TEMP VIEW v_findings AS SELECT * FROM main.v_findings WHERE tenant = {scope};
        CREATE TEMP VIEW doc_gaps   AS SELECT * FROM main.doc_gaps   WHERE tenant = {scope};
        """
    )
    return conn


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# Was a second, separate implementation of the same rounding. Now one function.
_paise = to_paise


def save_audit(
    result: AuditResult,
    ai_pipeline: bool = False,
    month: str | None = None,
    tenant: str = DEFAULT_TENANT,
) -> None:
    typical = result.typical
    room_deduction = sum(
        (d.amount for d in typical.policy_deductions if d.step.startswith("room_rent")),
        Decimal("0"),
    )

    with connect() as conn:
        # Children go first: ON DELETE CASCADE only fires on a parent delete, and the
        # parent row may not exist yet on a first insert.
        #
        # Every one of these carries the tenant. Without it, re-auditing CLM-001 for
        # one hospital deleted another hospital's CLM-001 and left the rest of that
        # claim's rows orphaned -- data loss across a security boundary, arriving as a
        # successful 200.
        scope = (tenant, result.claim_id)
        conn.execute("DELETE FROM findings WHERE tenant = ? AND claim_id = ?", scope)
        conn.execute("DELETE FROM doc_gaps WHERE tenant = ? AND claim_id = ?", scope)
        conn.execute("DELETE FROM claims   WHERE tenant = ? AND claim_id = ?", scope)

        conn.execute(
            """INSERT INTO claims (tenant, claim_id, month, diagnosis, procedure,
                   gross_bill_paise, settlement_paise, patient_liability_paise,
                   hospital_writeoff_paise, room_rent_deduction_paise, unmapped_count,
                   doc_gap_count, corpus_version, ai_pipeline)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                tenant,
                result.claim_id,
                # Callers pass the claim's own discharge month. The fallback used to be
                # a hardcoded "2026-07", which filed every audit run from the UI into
                # the same bucket and skewed the leakage dashboard's trend line.
                month or date.today().strftime("%Y-%m"),
                result.diagnosis,
                result.procedure,
                _paise(result.gross_bill),
                _paise(typical.projected_settlement),
                _paise(typical.patient_liability),
                _paise(typical.hospital_writeoff),
                _paise(room_deduction),
                result.unmapped_count,
                len(result.document_gaps),
                result.corpus_version,
                int(ai_pipeline),
            ),
        )
        conn.executemany(
            """INSERT INTO findings (tenant, claim_id, line_no, description, head,
                   classification, bearer, amount_paise, deducted_paise, cited_chunk_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    tenant,
                    result.claim_id,
                    f.line_no,
                    f.description,
                    f.head,
                    f.classification,
                    f.bearer,
                    _paise(f.amount),
                    _paise(f.deducted_amount),
                    f.cited_chunk_id,
                )
                for f in result.findings
            ],
        )
        conn.executemany(
            """INSERT INTO doc_gaps (tenant, claim_id, document_id, name, severity)
               VALUES (?,?,?,?,?)""",
            [
                (tenant, result.claim_id, g.document_id, g.name, g.severity)
                for g in result.document_gaps
            ],
        )


MONEY_COLUMNS = {
    "gross_bill",
    "settlement",
    "patient_liability",
    "hospital_writeoff",
    "room_rent_deduction",
    "amount",
    "deducted",
}


def _exact_money(frame: pd.DataFrame) -> pd.DataFrame:
    """Rebuild every money column as Decimal from its integer-paise source.

    The view's rupee columns are integer-divided and therefore truncated -- fine for a
    human skimming SQL, wrong for arithmetic. Anything that adds these numbers up gets
    them exact, which is the entire reason they are stored as paise.
    """
    for column in MONEY_COLUMNS & set(frame.columns):
        source = f"{column}_paise"
        if source in frame.columns:
            frame[column] = frame[source].map(from_paise)
    return frame


# The three dashboard readers. `tenant=None` spans every tenant, which is right for
# the single-tenant demo and wrong the moment keys are configured -- so the API passes
# the principal's own tenant and never a value from the request.
def claims_df(tenant: str | None = None) -> pd.DataFrame:
    with scoped_connection(tenant) as conn:
        return _exact_money(pd.read_sql_query("SELECT * FROM v_claims", conn))


def findings_df(tenant: str | None = None) -> pd.DataFrame:
    with scoped_connection(tenant) as conn:
        return _exact_money(pd.read_sql_query("SELECT * FROM v_findings", conn))


def doc_gaps_df(tenant: str | None = None) -> pd.DataFrame:
    with scoped_connection(tenant) as conn:
        return pd.read_sql_query("SELECT * FROM doc_gaps", conn)


def list_claims(
    query: str = "",
    month: str = "",
    limit: int = 50,
    offset: int = 0,
    tenant: str | None = None,
) -> tuple[list[dict], int]:
    """Audited claims, newest first, with the total before paging.

    The analytics surface was four aggregate endpoints and nothing that returned an
    individual claim, so the UI had no way to show history or reopen a past audit --
    the data was in the database and unreachable from the product.

    Parameterised throughout. `query` matches the claim id or the diagnosis.
    """
    where, params = [], []
    # First, and unconditionally when a tenant is given. Appending it last would work
    # identically today and would be one reordering away from being dropped.
    if tenant is not None:
        where.append("tenant = ?")
        params.append(tenant)
    if query.strip():
        where.append("(claim_id LIKE ? OR diagnosis LIKE ?)")
        params += [f"%{query.strip()}%"] * 2
    if month.strip():
        where.append("month = ?")
        params.append(month.strip())
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    with connect() as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute(f"SELECT COUNT(*) FROM claims {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT claim_id, month, audited_at, diagnosis, procedure,
                       gross_bill_paise, settlement_paise, patient_liability_paise,
                       hospital_writeoff_paise, room_rent_deduction_paise,
                       unmapped_count, doc_gap_count, corpus_version, ai_pipeline
                FROM claims {clause}
                ORDER BY audited_at DESC, claim_id
                LIMIT ? OFFSET ?""",
            [*params, max(1, min(limit, 200)), max(0, offset)],
        ).fetchall()

    return [
        {
            "claim_id": r["claim_id"],
            "month": r["month"],
            "audited_at": r["audited_at"],
            "diagnosis": r["diagnosis"] or "",
            "procedure": r["procedure"] or "",
            # Strings, not floats. JSON has no decimal type and this module exists to
            # keep money exact -- serialising it as a float here would undo that at the
            # last boundary, which is precisely the bug the rupee views used to have.
            "gross_bill": str(from_paise(r["gross_bill_paise"])),
            "settlement": str(from_paise(r["settlement_paise"])),
            "patient_liability": str(from_paise(r["patient_liability_paise"])),
            "hospital_writeoff": str(from_paise(r["hospital_writeoff_paise"])),
            "room_rent_deduction": str(from_paise(r["room_rent_deduction_paise"])),
            "unmapped_count": r["unmapped_count"],
            "doc_gap_count": r["doc_gap_count"],
            "corpus_version": r["corpus_version"],
            "ai_pipeline": bool(r["ai_pipeline"]),
        }
        for r in rows
    ], total


def claim_findings(claim_id: str, tenant: str | None = None) -> list[dict]:
    """Stored line-item findings for one claim."""
    scope, params = _tenant_clause(tenant, (claim_id,))
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""SELECT line_no, description, head, classification, bearer,
                       amount_paise, deducted_paise, cited_chunk_id
                FROM findings WHERE claim_id = ?{scope} ORDER BY line_no""",
            params,
        ).fetchall()
    return [
        {
            "line_no": r["line_no"],
            "description": r["description"],
            "head": r["head"],
            "classification": r["classification"],
            "bearer": r["bearer"],
            "amount": str(from_paise(r["amount_paise"])),
            "deducted_amount": str(from_paise(r["deducted_paise"])),
            "cited_chunk_id": r["cited_chunk_id"],
        }
        for r in rows
    ]


def claim_gaps(claim_id: str, tenant: str | None = None) -> list[dict]:
    scope, params = _tenant_clause(tenant, (claim_id,))
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT document_id, name, severity FROM doc_gaps WHERE claim_id = ?{scope}",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def months(tenant: str | None = None) -> list[str]:
    where = "" if tenant is None else " WHERE tenant = ?"
    params = () if tenant is None else (tenant,)
    with connect() as conn:
        return [
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT month FROM claims{where} ORDER BY month DESC", params
            )
        ]


def _tenant_clause(tenant: str | None, params: tuple) -> tuple[str, tuple]:
    """`(' AND tenant = ?', params + (tenant,))`, or the pair unchanged for None.

    One helper rather than the same three lines in four functions -- not for brevity
    but because a tenant predicate that is written out by hand each time is one that
    will eventually be written out incorrectly once.
    """
    if tenant is None:
        return "", params
    return " AND tenant = ?", (*params, tenant)


def reset() -> None:
    """Empty the store, whether or not something else currently has it open.

    Deleting the file is the cleaner reset and on Windows it is the one that fails:
    an open handle makes `unlink` raise `PermissionError`, and the process most likely
    to be holding one is the API -- which is running precisely when someone decides to
    re-seed. `python scripts/seed_db.py` therefore died with a permissions error at the
    one moment it was most obviously the right command to run.

    So: try the file, and fall back to emptying the tables in place. The second path
    leaves the schema and reclaims no disk, which for a store that is about to be
    refilled with the same data is no loss at all.
    """
    # WAL leaves two sidecar files next to the database. Deleting only the main file
    # leaves committed-but-uncheckpointed rows in the -wal, which SQLite replays into
    # the next database created at the same path -- so "reset" would not.
    try:
        for path in (DB_PATH, DB_PATH.with_suffix(DB_PATH.suffix + "-wal"),
                     DB_PATH.with_suffix(DB_PATH.suffix + "-shm")):
            path.unlink(missing_ok=True)
        return
    except OSError:
        pass

    with connect() as conn:
        # Children first: the FK is ON, so deleting parents before children would be
        # refused rather than cascaded -- CASCADE fires on a row delete, and this is
        # emptying tables.
        conn.execute("DELETE FROM findings")
        conn.execute("DELETE FROM doc_gaps")
        conn.execute("DELETE FROM claims")
